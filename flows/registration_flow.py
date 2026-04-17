import os
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from flows.manual_otp import wait_for_otp_completion
from sms_helper import SMSCodeFetcher
from utils import Account, human_delay


@dataclass(frozen=True)
class FlowConfig:
    target_url: str
    step_delay_ms: int
    jitter_ms: int
    human_pause_ms: int
    otp_timeout_seconds: int
    otp_poll_interval_ms: int
    sms_enabled: bool
    sms_fetcher: Optional[SMSCodeFetcher]
    sms_trace_id_prefix: str
    results_dir: str
    run_id: str


class RegistrationFlow:
    """
    可插拔流程：
    - 这里提供一个“模板实现”，尽量使用 role/text/label 选择器。
    - 你对接到自己的授权站点时，主要改 `_selectors_*` 与步骤函数。
    """

    def __init__(self, controller, flow_cfg: FlowConfig):
        self.controller = controller
        self.cfg = flow_cfg

    def _log(self, msg: str) -> None:
        print(f"[Flow] {msg}")

    def _delay(self) -> None:
        human_delay(self.cfg.step_delay_ms, self.cfg.jitter_ms)

    def _human_pause(self) -> None:
        human_delay(self.cfg.human_pause_ms, self.cfg.jitter_ms)

    def _goto_target_with_retry(self, page) -> None:
        attempts = [
            ("domcontentloaded", self.cfg.target_url),
            ("load", self.cfg.target_url),
            ("domcontentloaded", "https://dreamina.capcut.com/"),
            ("domcontentloaded", self.cfg.target_url),
        ]
        last_error: Optional[Exception] = None
        for idx, (wait_until, url) in enumerate(attempts, start=1):
            try:
                self._log(f"goto attempt {idx}: url={url} wait_until={wait_until}")
                page.goto(url, wait_until=wait_until, timeout=60000)
                self._log(f"goto attempt {idx}: success")
                return
            except Exception as e:
                last_error = e
                self._log(f"goto attempt {idx}: failed: {e}")
                page.wait_for_timeout(1200)
        raise RuntimeError(f"target navigation failed after retries: {last_error}")

    def _has_credential_fields(self, page) -> bool:
        try:
            if page.locator("input[type='email']").count() > 0:
                return True
        except Exception:
            pass
        try:
            if page.locator("input[type='password']").count() > 0:
                return True
        except Exception:
            pass
        for label in ["邮箱", "電子郵件", "Email", "E-mail", "密码", "密碼", "Password"]:
            try:
                if page.get_by_label(label).count() > 0:
                    return True
            except Exception:
                pass
        return False

    def _click_expand_sign_in_button(self, page) -> bool:
        try:
            btn = page.get_by_text("Continue with email", exact=True)
            count = btn.count()
            self._log(f"step_open_register: 'Continue with email' count={count}")
            if count <= 0:
                return False
            btn.first.scroll_into_view_if_needed()
            btn.first.click(timeout=5000)
            self._delay()
            self._log("step_open_register: clicked 'Continue with email'")
            final_btn = page.locator("span.new-forget-pwd-btn")
            final_count = final_btn.count()
            self._log(f"step_open_register: new-forget-pwd-btn count={final_count}")
            if final_count <= 0:
                return False
            final_btn.first.scroll_into_view_if_needed()
            final_btn.first.click(timeout=5000)
            self._delay()
            self._log("step_open_register: clicked 'new-forget-pwd-btn'")
            return True
        except Exception as e:
            self._log(f"step_open_register: click final steps failed: {e}")
            return False

    def run(self, acc: Account) -> tuple[bool, str]:
        page = None
        try:
            self._log(f"start run: {acc.email}")
            page = self.controller.new_page()
            page.set_default_timeout(30000)

            # Step 0: 打开入口
            self._log(f"goto target: {self.cfg.target_url}")
            self._goto_target_with_retry(page)
            self._log("target page loaded")
            self._delay()

            # Step 1: 进入“注册/创建账号”入口（示例：可改为你站点的登录/注册按钮）
            self._step_open_register(page)
            self._log("step_open_register: end")

            # Step 2: 输入账号密码并提交
            self._log("step_fill_credentials: begin")
            submit_ts_ms = self._step_fill_credentials(page, acc)
            self._log("step_fill_credentials: end")

            # Step 3: OTP/验证码（自动/人工）
            if self._is_on_otp_step(page):
                self._log("otp step detected")
                if self.cfg.sms_enabled and self.cfg.sms_fetcher:
                    # 传入当前时间作为参考，SMS获取器会自动调整（往前推60秒）
                    otp_baseline_ms = int(time.time() * 1000)
                    self._log(f"step_auto_otp: using reference time={otp_baseline_ms}")
                    ok, reason = self._step_auto_otp(page, acc, code_baseline_ms=otp_baseline_ms)
                    if not ok:
                        return False, reason
                else:
                    self._log("waiting manual otp completion")
                    wait_for_otp_completion(
                        page,
                        timeout_seconds=self.cfg.otp_timeout_seconds,
                        poll_interval_ms=self.cfg.otp_poll_interval_ms,
                        success_predicate=self._otp_success_predicate,
                    )

            # Step 4: 其它资料填写（示例：生日/偏好等）
            self._step_post_otp_profile(page)

            # Step 5: 成功判定
            if self._is_success(page):
                self._log("success marker detected")
                return True, "ok"
            self._log("success marker not detected")
            return False, "未检测到成功标识（请按实际页面更新 _is_success 规则）"
        except TimeoutError as e:
            self._log(f"timeout: {e}")
            p = self._try_screenshot(page, prefix="timeout", email=acc.email)
            suffix = f" | screenshot={p}" if p else ""
            return False, f"超时: {e}{suffix}"
        except Exception as e:
            self._log(f"exception: {type(e).__name__}: {e}")
            p = self._try_screenshot(page, prefix="error", email=acc.email)
            suffix = f" | screenshot={p}" if p else ""
            return False, f"异常: {type(e).__name__}: {e}{suffix}"
        finally:
            self._log("closing page")
            self.controller.close_page(page)

    # -----------------
    # 下面的步骤是“示例模板”，需要按你的授权站点 DOM 做定制
    # -----------------

    def _screenshot_dir(self) -> str:
        return os.path.join(self.cfg.results_dir, "screenshots", self.cfg.run_id)

    def _try_screenshot(self, page, *, prefix: str, email: str) -> Optional[str]:
        if not page:
            return None
        try:
            os.makedirs(self._screenshot_dir(), exist_ok=True)
            safe = email.replace("@", "_").replace(":", "_").replace("/", "_")
            ts = int(time.time())
            path = os.path.join(self._screenshot_dir(), f"{prefix}_{safe}_{ts}.png")
            page.screenshot(path=path, full_page=True)
            return path
        except Exception:
            return None

    def _step_auto_otp(self, page, acc: Account, code_baseline_ms: Optional[int] = None) -> tuple[bool, str]:
        self._log("step_auto_otp: begin")
        prefix = (self.cfg.sms_trace_id_prefix or "trace").strip()
        trace_id = f"{prefix}-{uuid.uuid4().hex[:8]}"
        self._log(f"step_auto_otp: waiting code for {acc.email}, trace_id={trace_id}")
        code = self.cfg.sms_fetcher.wait_for_code(  # type: ignore[union-attr]
            acc.email,
            trace_id,
            baseline_ms=code_baseline_ms,
        )
        if not code:
            self._log(f"step_auto_otp: no code received, trace_id={trace_id}")
            p = self._try_screenshot(page, prefix="otp_no_code", email=acc.email)
            suffix = f" | screenshot={p}" if p else ""
            return False, f"验证码获取失败（trace_id={trace_id}）{suffix}"

        self._log(f"step_auto_otp: code received, len={len(code)}")
        if not self._fill_otp_code(page, code):
            self._log("step_auto_otp: fill code failed")
            p = self._try_screenshot(page, prefix="otp_fill_failed", email=acc.email)
            suffix = f" | screenshot={p}" if p else ""
            return False, f"验证码填写失败（code={code} trace_id={trace_id}）{suffix}"

        # 关键：等待 OTP 验证完成并页面跳转
        self._log("step_auto_otp: waiting for OTP verification and page transition...")
        try:
            # 等待页面不再是 OTP 页面（最多等待 5 秒，减少等待时间）
            for attempt in range(10):  # 10 * 500ms = 5s
                page.wait_for_timeout(500)
                
                # 检查是否还在 OTP 页面
                if not self._is_on_otp_step(page):
                    self._log(f"step_auto_otp: OTP verification completed, left OTP page at attempt {attempt + 1}")
                    break
                
                if attempt == 9:
                    self._log("step_auto_otp: WARNING - still on OTP page after 5s, proceeding anyway")
        except Exception as e:
            self._log(f"step_auto_otp: error waiting for page transition: {e}")
            # 不抛出异常，继续执行
        
        self._log("step_auto_otp: code Success{code}, input year month day")
        return True, "otp_ok"

    def _fill_otp_code(self, page, code: str) -> bool:
        code = (code or "").strip()
        if len(code) != 6:
            self._log(f"step_auto_otp: invalid code length={len(code)}")
            return False

        self._log("step_auto_otp: targeting OTP hidden input...")

        # 1. 点击第一个视觉框，强制将焦点/上下文拉回弹窗内部（彻底解决背景滚动问题）
        try:
            page.locator("div.verification_code_input-number").first.click(timeout=3000)
            page.wait_for_timeout(200)  # 等待前端焦点切换完成
        except Exception as e:
            self._log(f"step_auto_otp: click visual box failed: {e}")

        # 2. 定位底部真实的隐藏输入框
        # 使用 maxlength=6 精准匹配，避免误触其他 input
        hidden_input = page.locator("input[maxlength='6']")

        try:
            hidden_input.wait_for(state="attached", timeout=3000)

            # 方案A：Playwright 原生 fill (对 opacity:0 的元素通常可直接操作)
            try:
                hidden_input.fill(code)
                self._log("step_auto_otp: native fill() succeeded")
                return True
            except Exception as e:
                self._log(f"step_auto_otp: native fill blocked, fallback to JS: {e}")

            # 方案B：JS 强制赋值 + 派发事件 (100% 兼容 React/Vue/AntDesign 受控组件)
            hidden_input.evaluate("""(el, code) => {
                el.focus();
                el.value = code;
                // 触发框架必需的 input 和 change 事件，使视觉 Div 同步更新
                el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertFromPaste' }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                // 兼容部分严格校验库的 keyup 监听
                el.dispatchEvent(new KeyboardEvent('keyup', { key: code.slice(-1), bubbles: true }));
            }""", code)
            self._log("step_auto_otp: JS direct injection succeeded")
            return True

        except Exception as e:
            self._log(f"step_auto_otp: locate hidden input failed: {e}")
            return False

    def _click_otp_submit_if_any(self, page) -> None:
        # 尽量用 role/button 的候选文本点击；找不到就不报错
        candidates = ["提交", "验证", "驗證", "继续", "繼續", "Continue", "Next", "Verify", "Submit"]
        for text in candidates:
            try:
                btn = page.get_by_role("button", name=text)
                if btn.count() > 0:
                    btn.first.click()
                    return
            except Exception:
                pass

    def _step_open_register(self, page) -> None:
        """
        默认策略：
        - 优先点击 class 包含 content-Atv29u 的入口容器
        - 优先找含 “注册/Sign in/Create” 的链接或按钮
        - 找不到则不报错（留给后续步骤/自定义）
        """
        
        self._log("step_open_register: begin")
        self._human_pause()

        try:
            # 1) 先检查是否已经进入目标状态（避免重复操作）
            if page.locator("div[class*='lv-spin-children']").count() > 0:
                self._log("step_open_register: already entered (lv-spin-children matched)")
                if self._click_expand_sign_in_button(page):
                    return
        except Exception as e:
            self._log(f"step_open_register: pre-check failed: {e}")

        # 2) 核心逻辑：等待并点击 #AIGeneratedRecord
        try:
            self._log("step_open_register: waiting for div#AIGeneratedRecord")
            target = page.locator("div#AIGeneratedRecord")
            
            # 等待元素出现（可配置超时，这里用 15 秒）
            target.wait_for(state="visible", timeout=15000)
            target.scroll_into_view_if_needed()
            target.click(timeout=5000)
            
            self._log("step_open_register: clicked div#AIGeneratedRecord")
            self._delay()
            
            # 3) 点击后尝试展开登录/注册表单（保留原有逻辑）
            if self._click_expand_sign_in_button(page):
                self._log("step_open_register: expand button clicked successfully")
                return
                
        except Exception as e:
            self._log(f"step_open_register: click div#AIGeneratedRecord failed: {e}")
            # 可选：失败时截图便于排查
            # self._try_screenshot(page, prefix="open_register_failed", email="unknown")
            raise

        self._log("step_open_register: end (no action taken or fallback)")

    def _step_fill_credentials(self, page, acc: Account) -> Optional[int]:
        """
        示例：寻找 email/password 输入框并提交。
        你对接时建议用更明确的 label/placeholder/aria-label。
        """
        self._log("step_fill_credentials: locating email input by placeholder='Enter email'")
        self._delay()
        email_filled = False
        try:
            email_input = page.locator("input[placeholder='Enter email']")
            email_count = email_input.count()
            self._log(f"step_fill_credentials: email input count={email_count}")
            if email_count > 0:
                email_input.first.fill(acc.email)
                email_filled = True
                self._log("step_fill_credentials: email filled")
        except Exception as e:
            self._log(f"step_fill_credentials: email fill failed: {e}")

        if not email_filled:
            self._log("step_fill_credentials: email input not found with placeholder")

        self._log("step_fill_credentials: locating password input by placeholder='Enter password'")
        self._delay()
        password_filled = False
        try:
            pwd_input = page.locator("input[placeholder='Enter password']")
            pwd_count = pwd_input.count()
            self._log(f"step_fill_credentials: password input count={pwd_count}")
            if pwd_count > 0:
                pwd_input.first.fill(acc.password)
                password_filled = True
                self._log("step_fill_credentials: password filled")
        except Exception as e:
            self._log(f"step_fill_credentials: password fill failed: {e}")

        if not password_filled:
            self._log("step_fill_credentials: password input not found with placeholder")

        self._human_pause()
        self._log("step_fill_credentials: locating submit button text='Continue'")
        try:
            btn = page.get_by_role("button", name="Continue")
            btn_count = btn.count()
            self._log(f"step_fill_credentials: continue button count={btn_count}")
            if btn_count > 0:
                btn.first.click()
                self._log("step_fill_credentials: clicked continue button")
                submit_ts_ms = int(time.time() * 1000)
                self._log(f"step_fill_credentials: submit timestamp(ms)={submit_ts_ms}")
                self._delay()
                return submit_ts_ms
        except Exception as e:
            self._log(f"step_fill_credentials: click continue failed: {e}")

        self._log("step_fill_credentials: continue button not clicked")
        return None

    def _is_on_otp_step(self, page) -> bool:
        # 提交账号密码后，OTP 区域可能有异步渲染，做短轮询提高命中率
        for attempt in range(10):
            # 1) 文案判定
            for text in [
                "verification code",
                "Verification code",
                "Enter code",
                "OTP",
                "验证码",
                "驗證碼",
            ]:
                try:
                    c = page.get_by_text(text).count()
                    if c > 0:
                        self._log(f"otp detect: matched text '{text}', count={c}, attempt={attempt + 1}")
                        return True
                except Exception:
                    pass

            # 2) 输入框特征判定（单框/分格）
            selectors = [
                "input[autocomplete='one-time-code']",
                "input[name*='otp' i]",
                "input[id*='otp' i]",
                "input[placeholder*='code' i]",
                "input[inputmode='numeric']",
                "input[type='tel']",
            ]
            for sel in selectors:
                try:
                    c = page.locator(sel).count()
                    if c > 0:
                        self._log(f"otp detect: matched selector '{sel}', count={c}, attempt={attempt + 1}")
                        return True
                except Exception:
                    pass

            # 3) 多个 maxlength=1 的分格验证码输入
            try:
                single_char_inputs = page.locator("input[maxlength='1']").count()
                if single_char_inputs >= 4:
                    self._log(
                        f"otp detect: matched split otp inputs count={single_char_inputs}, attempt={attempt + 1}"
                    )
                    return True
            except Exception:
                pass

            if attempt < 9:
                page.wait_for_timeout(400)

        self._log("otp detect: not on otp step after polling")
        return False

    def _otp_success_predicate(self, page) -> bool:
        # 模板：当 OTP 通过后通常会出现“生日/资料/欢迎”等下一步页面
        keywords = ["birthday", "出生", "欢迎", "Welcome", "profile", "资料", "設定", "设置"]
        for k in keywords:
            try:
                if page.get_by_text(k).count() > 0:
                    return True
            except Exception:
                pass
        return False

    def _step_post_otp_profile(self, page) -> None:
        """
        填写生日信息（必须大于18岁）
        适配 CapCut/Dreamina 的 lv-select 组件
        """
        self._log("step_post_otp_profile: begin")
        
        # 快速检查页面状态（减少等待时间）
        page.wait_for_timeout(300)
        
        # 检查当前 URL 和页面状态
        current_url = page.url
        self._log(f"step_post_otp_profile: current URL: {current_url}")
        
        # 检查是否还在 OTP 页面（说明 OTP 验证可能失败）
        if self._is_on_otp_step(page):
            self._log("step_post_otp_profile: ERROR - still on OTP page!")
            p = self._try_screenshot(page, prefix="still_on_otp_page", email="unknown")
            suffix = f" | screenshot={p}" if p else ""
            raise RuntimeError(f"OTP 验证后仍停留在验证码页面，可能验证码错误或验证失败{suffix}")
        
        # 使用较短的 human_pause
        self._delay()
        
        # 检查是否在生日填写页面
        try:
            birthday_text_found = page.get_by_text("When's your birthday?").count() > 0
            if not birthday_text_found:
                # 尝试其他可能的文本
                alternative_texts = ["生日", "Birthday", "birth date"]
                for alt_text in alternative_texts:
                    if page.get_by_text(alt_text).count() > 0:
                        self._log(f"step_post_otp_profile: found alternative text: '{alt_text}'")
                        birthday_text_found = True
                        break
                
                if not birthday_text_found:
                    self._log("step_post_otp_profile: not on birthday page, skip")
                    return
        except Exception as e:
            self._log(f"step_post_otp_profile: error checking birthday page: {e}")
            return
        
        # 生成大于18岁的生日（18-28岁之间随机）
        from datetime import datetime, timedelta
        import random
        
        today = datetime.now()
        years_ago = random.randint(18, 28)
        random_days = random.randint(0, 364)
        birth_date = today - timedelta(days=years_ago*365 + random_days)
        
        year = str(birth_date.year)
        month_num = birth_date.month    # 1-12
        day = str(birth_date.day)       # 1-31
        
        self._log(f"step_post_otp_profile: generated birthday: {year}-{month_num}-{day} (age: {years_ago})")
        
        try:
            # ========== 1. 填写年份 ==========
            year_input = page.locator("input[placeholder='Year']")
            if year_input.count() > 0:
                year_input.first.fill(year)
                self._log(f"step_post_otp_profile: year filled: {year}")
                
                # 减少验证等待时间
                page.wait_for_timeout(200)
                
                # 等待月份下拉框变为可交互状态（减少等待）
                page.wait_for_timeout(300)
            else:
                self._log("step_post_otp_profile: year input not found")
            
            # ========== 2. 选择月份（英文月份名）==========
            month_name = self._month_num_to_en(month_num)
            self._log(f"step_post_otp_profile: selecting month: {month_name}")
            if not self._select_lv_option(page, placeholder="Month", option_text=month_name):
                self._log("step_post_otp_profile: month selection failed - CRITICAL ERROR")
                p = self._try_screenshot(page, prefix="birthday_month_failed", email="unknown")
                suffix = f" | screenshot={p}" if p else ""
                raise RuntimeError(f"生日月份选择失败{suffix}")
            self._log(f"step_post_otp_profile: month selected: {month_name}")
            
            # 等待日期下拉框变为可交互状态（减少等待）
            page.wait_for_timeout(200)
            
            # ========== 3. 选择日期（数字）==========
            self._log(f"step_post_otp_profile: selecting day: {day}")
            if not self._select_lv_option(page, placeholder="Day", option_text=day):
                self._log("step_post_otp_profile: day selection failed - CRITICAL ERROR")
                p = self._try_screenshot(page, prefix="birthday_day_failed", email="unknown")
                suffix = f" | screenshot={p}" if p else ""
                raise RuntimeError(f"生日日期选择失败{suffix}")
            self._log(f"step_post_otp_profile: day selected: {day}")
            
            page.wait_for_timeout(150)
            
            # ========== 4. 点击 Next 按钮 ==========
            next_btn = page.locator("button.lv_new_sign_in_panel_wide-birthday-next")
            if next_btn.count() > 0:
                try:
                    next_btn.first.wait_for(state="enabled", timeout=5000)
                except Exception:
                    self._log("step_post_otp_profile: Next button still disabled, waiting...")
                    page.wait_for_timeout(1000)
                
                if "lv-btn-disabled" not in (next_btn.first.get_attribute("class", timeout=2000) or ""):
                    next_btn.first.click()
                    self._log("step_post_otp_profile: clicked Next button")
                    self._delay()
                else:
                    self._log("step_post_otp_profile: Next button still disabled after fill")
            else:
                self._log("step_post_otp_profile: Next button not found")
            
            self._log("step_post_otp_profile: completed successfully")
            
        except RuntimeError:
            # 重新抛出 RuntimeError（我们的自定义错误）
            raise
        except Exception as e:
            self._log(f"step_post_otp_profile: failed: {e}")
            p = self._try_screenshot(page, prefix="birthday_error", email="unknown")
            suffix = f" | screenshot={p}" if p else ""
            raise RuntimeError(f"生日填写异常: {e}{suffix}")

    def _month_num_to_en(self, month_num: int) -> str:
        """数字月份转英文全称"""
        months = [
            "", "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ]
        return months[month_num] if 1 <= month_num <= 12 else ""

    def _select_lv_option(self, page, *, placeholder: str, option_text: str) -> bool:
        """
        通用方法：操作 lv-select 组件的下拉选择
        - placeholder: 输入框的 placeholder，如 "Month" / "Day"
        - option_text: 要选择的选项文本（精确匹配），如 "March" / "9"
        """
        try:
            # 1. 定位 combobox 容器（通过 placeholder 关联）
            combobox = page.locator(f"div[role='combobox']").filter(
                has=page.locator(f"input[placeholder='{placeholder}']")
            ).first
            
            # 2. 点击打开下拉菜单
            combobox.click(timeout=3000)
            page.wait_for_timeout(500)  # 等待 popup 动画 + 选项渲染
            
            # 3. 等待选项列表出现（lv-select-popup-xxx）
            popup = page.locator("div[id^='lv-select-popup']").first
            try:
                popup.wait_for(state="visible", timeout=3000)
            except Exception:
                self._log(f"_select_lv_option: popup not visible for {placeholder}")
                return False
            
            # 4. 精确匹配选项文本（li[role="option"].lv-select-option）
            # 使用 text_content() 确保精确匹配，避免 "1" 匹配到 "10"
            options = popup.locator("li[role='option'].lv-select-option").all()
            for opt in options:
                try:
                    text = opt.text_content().strip()
                    if text == option_text:
                        opt.scroll_into_view_if_needed()
                        opt.click(timeout=3000)
                        self._log(f"_select_lv_option: selected {placeholder}='{option_text}'")
                        return True
                except Exception:
                    continue
            
            self._log(f"_select_lv_option: option '{option_text}' not found for {placeholder}")
            return False
            
        except Exception as e:
            self._log(f"_select_lv_option: error for {placeholder}='{option_text}': {e}")
            return False

    def _is_success(self, page) -> bool:
        """
        检测并完成角色选择对话框（如果有）
        - 等待角色选择对话框弹出
        - 随机选择一个职业角色
        - 点击 "Continue to Dreamina" 按钮
        - 验证是否真正进入主页面
        - 返回是否成功完成
        """
        self._log("step_is_success: checking for role selection dialog...")
        
        try:
            # 1. 等待角色选择对话框出现（最多等待 5 秒）
            has_role_dialog = False
            try:
                dialog = page.locator("div[role='dialog']").filter(
                    has=page.get_by_text("What role best describes you?")
                ).first
                dialog.wait_for(state="visible", timeout=5000)
                self._log("step_is_success: role selection dialog detected")
                has_role_dialog = True
            except Exception:
                self._log("step_is_success: no role selection dialog found")
            
            if has_role_dialog:
                # 2. 随机选择一个职业角色（避免总是选第一个）
                import random
                role_options = [
                    "Art professional",
                    "Designer", 
                    "TV and film industry professional",
                    "Digital marketing and e-commerce professional",
                    "Social media content creator",
                    "Tech professional",
                    "Other (please specify)"
                ]
                selected_role = random.choice(role_options)
                self._log(f"step_is_success: selecting role: {selected_role}")
                
                # 3. 点击选中的角色选项
                try:
                    role_element = page.get_by_text(selected_role).first
                    role_element.scroll_into_view_if_needed()
                    role_element.click(timeout=3000)
                    page.wait_for_timeout(300)  # 等待选中状态更新
                    self._log(f"step_is_success: clicked role '{selected_role}'")
                except Exception as e:
                    self._log(f"step_is_success: failed to click role '{selected_role}': {e}")
                    # 降级方案：点击第一个选项
                    try:
                        first_option = page.locator("div.question-option-Pvs1Wx").first
                        first_option.click(timeout=3000)
                        page.wait_for_timeout(300)
                        self._log("step_is_success: clicked first role option (fallback)")
                    except Exception as e2:
                        self._log(f"step_is_success: fallback also failed: {e2}")
                
                # 4. 点击 "Continue to Dreamina" 按钮
                try:
                    continue_btn = page.get_by_role("button", name="Continue to Dreamina")
                    if continue_btn.count() > 0:
                        # 等待按钮启用（选中角色后按钮会从 disabled 变为 enabled）
                        try:
                            continue_btn.first.wait_for(state="enabled", timeout=3000)
                        except Exception:
                            self._log("step_is_success: waiting for button to be enabled...")
                            page.wait_for_timeout(1000)
                        
                        if "lv-btn-disabled" not in (continue_btn.first.get_attribute("class", timeout=2000) or ""):
                            continue_btn.first.click()
                            self._log("step_is_success: clicked 'Continue to Dreamina' button")
                            page.wait_for_timeout(2000)  # 等待页面跳转
                        else:
                            self._log("step_is_success: Continue button still disabled")
                            return False
                    else:
                        self._log("step_is_success: 'Continue to Dreamina' button not found")
                        return False
                except Exception as e:
                    self._log(f"step_is_success: failed to click continue button: {e}")
                    return False
            
            # 5. 关键：验证是否真正注册成功并进入主页面
            # 等待页面加载完成
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                self._log("step_is_success: network idle timeout, continuing with checks")
            
            # 检查URL是否包含主页特征
            current_url = page.url
            self._log(f"step_is_success: current URL: {current_url}")
            
            # 如果还在登录/注册相关页面，说明注册失败
            error_url_patterns = [
                "/login",
                "/sign-in",
                "/auth",
            ]
            
            for pattern in error_url_patterns:
                if pattern in current_url:
                    self._log(f"step_is_success: ERROR - URL contains '{pattern}', registration failed")
                    p = self._try_screenshot(page, prefix="still_on_login_page", email="unknown")
                    suffix = f" | screenshot={p}" if p else ""
                    raise RuntimeError(f"注册失败，仍在登录页面{suffix}")
            
            # 检查页面内容是否有错误提示
            error_indicators = [
                "Sign in",
                "Log in",
                "Enter email",
                "verification code",
                "When's your birthday",
                "What role best describes you",
                "Incorrect email or password",
                "Invalid credentials",
            ]
            
            for indicator in error_indicators:
                try:
                    if page.get_by_text(indicator).count() > 0:
                        self._log(f"step_is_success: ERROR - still on registration page, found '{indicator}'")
                        p = self._try_screenshot(page, prefix="registration_incomplete", email="unknown")
                        suffix = f" | screenshot={p}" if p else ""
                        raise RuntimeError(f"注册未完成，仍停留在{indicator}页面{suffix}")
                except RuntimeError:
                    raise
                except Exception:
                    pass
            
            # 检查成功标志
            success_markers = [
                "Start Creating",
                "Create video",
                "AI Tools",
                "Home",
                "Dashboard",
            ]
            
            # 如果URL明确指向主页且没有错误标志，认为成功
            if "dreamina.capcut.com" in current_url and "/ai-tool" in current_url:
                # 进一步验证：检查是否有主页特有的元素
                try:
                    # 尝试查找主页特有的元素（根据你的实际页面调整）
                    home_indicators = [
                        "Start Creating",
                        "Create video", 
                        "AI Tools",
                    ]
                    
                    for marker in home_indicators:
                        if page.get_by_text(marker).count() > 0:
                            self._log(f"step_is_success: confirmed success with marker '{marker}'")
                            return True
                    
                    # 如果没有找到明确的标志，但URL正确且没有错误标志，也认为成功
                    self._log("step_is_success: URL indicates success and no error markers found")
                    return True
                except Exception as e:
                    self._log(f"step_is_success: error checking home indicators: {e}")
                    return False
            
            # 检查其他成功标志
            for marker in success_markers:
                try:
                    if page.get_by_text(marker).count() > 0:
                        self._log(f"step_is_success: success marker '{marker}' detected")
                        return True
                except Exception:
                    pass
            
            self._log("step_is_success: no clear success markers found")
            return False
            
        except RuntimeError:
            raise
        except Exception as e:
            self._log(f"step_is_success: error occurred: {e}")
            self._try_screenshot(page, prefix="role_selection_error", email="unknown")
            return False

