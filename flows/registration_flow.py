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
                    ok, reason = self._step_auto_otp(page, acc, code_baseline_ms=submit_ts_ms)
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
        self._human_pause()
        
        # 检查是否在生日填写页面
        # try:
        #     if page.get_by_text("When's your birthday?").count() == 0:
        #         self._log("step_post_otp_profile: not on birthday page, skip")
        #         return
        # except Exception:
        #     self._log("step_post_otp_profile: birthday page not detected, skip")
        #     return
        
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
                page.wait_for_timeout(300)
            
            # ========== 2. 选择月份（英文月份名）==========
            month_name = self._month_num_to_en(month_num)
            self._log(f"step_post_otp_profile: selecting month: {month_name}")
            if self._select_lv_option(page, placeholder="Month", option_text=month_name):
                self._log(f"step_post_otp_profile: month selected: {month_name}")
            else:
                self._log("step_post_otp_profile: month selection failed")
                return
            
            page.wait_for_timeout(200)
            
            # ========== 3. 选择日期（数字）==========
            self._log(f"step_post_otp_profile: selecting day: {day}")
            if self._select_lv_option(page, placeholder="Day", option_text=day):
                self._log(f"step_post_otp_profile: day selected: {day}")
            else:
                self._log("step_post_otp_profile: day selection failed")
                return
            
            page.wait_for_timeout(300)
            
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
            
            self._log("step_post_otp_profile: completed successfully")
            
        except Exception as e:
            self._log(f"step_post_otp_profile: failed: {e}")
            self._try_screenshot(page, prefix="birthday_error", email="unknown")
            raise

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
        # 模板：根据你站点的“注册成功”标识来改
        for k in ["Welcome", "欢迎", "Dashboard", "控制台", "Home"]:
            try:
                if page.get_by_text(k).count() > 0:
                    return True
            except Exception:
                pass
        return False

