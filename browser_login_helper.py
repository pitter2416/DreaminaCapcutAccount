#!/usr/bin/env python3
"""
独立的浏览器登录脚本
用于在子进程中执行Playwright浏览器自动化，避免asyncio冲突
"""

import sys
import json
from playwright.sync_api import sync_playwright

def login_2925(username: str, password: str, login_url: str) -> dict:
    """
    通过浏览器登录2925邮箱
    
    Returns:
        dict: {
            "success": bool,
            "token": str or None,
            "cookies": list
        }
    """
    result = {
        "success": False,
        "token": None,
        "cookies": []
    }
    
    try:
        with sync_playwright() as p:
            # 启动浏览器（有头模式）
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            
            # 打开登录页面
            print(f"[BrowserLogin] 打开登录页面: {login_url}", flush=True)
            page.goto(login_url, timeout=30000)
            page.wait_for_timeout(2000)
            
            # 步骤1: 填写用户名
            print(f"[BrowserLogin] 填写用户名: {username}", flush=True)
            try:
                username_input = page.locator("input[type='text']")
                if username_input.count() >= 1:
                    username_input.first.fill(username)
                    print("[BrowserLogin] ✅ 用户名已填写", flush=True)
                else:
                    print("[BrowserLogin] ❌ 未找到用户名输入框", flush=True)
                    browser.close()
                    return result
            except Exception as e:
                print(f"[BrowserLogin] ❌ 填写用户名失败: {e}", flush=True)
                browser.close()
                return result
            page.wait_for_timeout(500)
            
            # 步骤2: 填写密码
            print("[BrowserLogin] 填写密码", flush=True)
            
            # 尝试多种方式找到密码输入框
            password_filled = False
            
            # 方法1: placeholder='密码'
            try:
                password_input = page.locator("input[placeholder='密码']")
                if password_input.count() > 0:
                    password_input.first.fill(password)
                    print("[BrowserLogin] ✅ 密码已填写（通过placeholder）", flush=True)
                    password_filled = True
            except Exception as e:
                print(f"[BrowserLogin] ⚠️ 方法1失败: {e}", flush=True)
            
            # 方法2: type='password'
            if not password_filled:
                try:
                    password_input = page.locator("input[type='password']")
                    if password_input.count() > 0:
                        password_input.first.fill(password)
                        print("[BrowserLogin] ✅ 密码已填写（通过type）", flush=True)
                        password_filled = True
                except Exception as e:
                    print(f"[BrowserLogin] ⚠️ 方法2失败: {e}", flush=True)
            
            # 方法3: 第二个input元素
            if not password_filled:
                try:
                    all_inputs = page.locator("input")
                    count = all_inputs.count()
                    print(f"[BrowserLogin] 找到 {count} 个 input 元素", flush=True)
                    if count >= 2:
                        all_inputs.nth(1).fill(password)
                        print("[BrowserLogin] ✅ 密码已填写（第二个input）", flush=True)
                        password_filled = True
                    else:
                        print(f"[BrowserLogin] ⚠️ input元素数量不足: {count}", flush=True)
                except Exception as e:
                    print(f"[BrowserLogin] ⚠️ 方法3失败: {e}", flush=True)
            
            # 如果所有方法都失败
            if not password_filled:
                print("[BrowserLogin] ❌ 所有方法都无法填写密码", flush=True)
                screenshot_path = "Results/browser_login_password_not_found.png"
                page.screenshot(path=screenshot_path)
                print(f"[BrowserLogin] 已保存调试截图: {screenshot_path}", flush=True)
                browser.close()
                return result
            
            page.wait_for_timeout(500)
            
            # 步骤3: 勾选服务协议
            print("[BrowserLogin] 勾选服务协议", flush=True)
            
            # 方法1: 直接点击 el-checkbox__inner span
            try:
                checkbox_inner = page.locator(".login-agrement .el-checkbox__inner")
                if checkbox_inner.count() > 0:
                    # 检查是否已勾选（通过查看父元素是否有is-checked类）
                    parent_class = checkbox_inner.first.evaluate("el => el.parentElement.className")
                    is_checked = "is-checked" in parent_class
                    print(f"[BrowserLogin] 当前勾选状态: {is_checked}", flush=True)
                    
                    if not is_checked:
                        checkbox_inner.first.click()
                        print("[BrowserLogin] ✅ 已点击 el-checkbox__inner", flush=True)
                        page.wait_for_timeout(300)
                        
                        # 验证是否勾选成功
                        parent_class_after = checkbox_inner.first.evaluate("el => el.parentElement.className")
                        is_checked_after = "is-checked" in parent_class_after
                        print(f"[BrowserLogin] 勾选后状态: {is_checked_after}", flush=True)
                        if is_checked_after:
                            print("[BrowserLogin] ✅ 服务协议勾选成功（方法1）", flush=True)
                        else:
                            print("[BrowserLogin] ⚠️ 勾选可能未成功", flush=True)
                    else:
                        print("[BrowserLogin] ℹ️ 服务协议已勾选", flush=True)
                else:
                    print("[BrowserLogin] ⚠️ 未找到 el-checkbox__inner", flush=True)
            except Exception as e:
                print(f"[BrowserLogin] ⚠️ 方法1失败: {e}", flush=True)
            
            # 方法2: 点击整个 label
            try:
                agreement_label = page.locator(".login-agrement .el-checkbox")
                if agreement_label.count() > 0:
                    is_checked_before = agreement_label.first.evaluate("el => el.classList.contains('is-checked')")
                    if not is_checked_before:
                        agreement_label.first.click()
                        print("[BrowserLogin] ✅ 已点击 el-checkbox label（方法2）", flush=True)
                        page.wait_for_timeout(300)
                    else:
                        print("[BrowserLogin] ℹ️ 服务协议已勾选", flush=True)
                else:
                    print("[BrowserLogin] ⚠️ 未找到 el-checkbox label", flush=True)
            except Exception as e:
                print(f"[BrowserLogin] ⚠️ 方法2失败: {e}", flush=True)
            
            # 方法3: 通过文本查找并点击
            try:
                agree_text = page.locator("text=我已阅读并同意")
                if agree_text.count() > 0:
                    # 找到包含该文本的label，然后点击它
                    agree_element = agree_text.first
                    # 向上查找最近的 label.el-checkbox
                    parent_label = agree_element.evaluate("""
                        el => {
                            let parent = el.parentElement;
                            while (parent && !parent.classList.contains('el-checkbox')) {
                                parent = parent.parentElement;
                            }
                            return parent;
                        }
                    """)
                    
                    if parent_label:
                        # 使用JavaScript点击
                        page.evaluate("el => el.click()", parent_label)
                        print("[BrowserLogin] ✅ 已通过文本定位并点击（方法3）", flush=True)
                        page.wait_for_timeout(300)
                    else:
                        print("[BrowserLogin] ⚠️ 未找到父级label", flush=True)
                else:
                    print("[BrowserLogin] ⚠️ 未找到'我已阅读并同意'文本", flush=True)
            except Exception as e:
                print(f"[BrowserLogin] ⚠️ 方法3失败: {e}", flush=True)
            
            page.wait_for_timeout(500)
            
            # 步骤4: 点击登录按钮
            print("[BrowserLogin] 点击登录按钮", flush=True)
            login_button = page.locator("button.submit-button")
            if login_button.count() > 0:
                login_button.first.click()
                print("[BrowserLogin] ✅ 已点击登录按钮", flush=True)
            else:
                login_button = page.locator("button:has-text('登录')")
                if login_button.count() > 0:
                    login_button.first.click()
                    print("[BrowserLogin] ✅ 已点击登录按钮", flush=True)
                else:
                    print("[BrowserLogin] ❌ 未找到登录按钮", flush=True)
                    browser.close()
                    return result
            
            # 步骤5: 等待登录完成并捕获Token
            print("[BrowserLogin] 等待登录完成并捕获Token...", flush=True)
            
            # 监听网络请求，捕获authorization header
            captured_token = None
            
            def handle_response(response):
                nonlocal captured_token
                try:
                    # 检查响应头或请求头中是否有authorization
                    headers = response.request.headers
                    auth_header = headers.get('authorization') or headers.get('Authorization')
                    if auth_header and auth_header.startswith('Bearer '):
                        token = auth_header.replace('Bearer ', '')
                        print(f"[BrowserLogin] ✅ 从请求头捕获到 token (长度: {len(token)})", flush=True)
                        captured_token = token
                except Exception as e:
                    pass
            
            page.on("response", handle_response)
            
            # 等待URL变化，离开登录页
            try:
                page.wait_for_url(lambda url: "login" not in url.lower(), timeout=15000)
                print(f"[BrowserLogin] ✅ 登录成功，当前URL: {page.url}", flush=True)
            except:
                page.wait_for_timeout(3000)
                if "login" not in page.url.lower():
                    print(f"[BrowserLogin] ✅ 登录成功，当前URL: {page.url}", flush=True)
                else:
                    print("[BrowserLogin] ❌ 登录失败，仍在登录页面", flush=True)
                    screenshot_path = "Results/login_failed_debug.png"
                    page.screenshot(path=screenshot_path)
                    print(f"[BrowserLogin] 已保存调试截图: {screenshot_path}", flush=True)
                    browser.close()
                    return result
            
            # 等待一下，确保所有请求都完成
            page.wait_for_timeout(2000)
            
            # 步骤6: 提取Token
            print("[BrowserLogin] 尝试提取Token...", flush=True)
            
            # 优先使用从网络请求中捕获的token
            if captured_token:
                print(f"[BrowserLogin] ✅ 使用从网络请求捕获的 token (长度: {len(captured_token)})", flush=True)
                result["token"] = captured_token
            else:
                # 方法1: 从 localStorage 获取 token
                try:
                    token = page.evaluate("""() => {
                        const keys = ['token', 'accessToken', 'access_token', 'jwt', 'auth_token', 'Authorization', 'user_token'];
                        for (const key of keys) {
                            const value = localStorage.getItem(key);
                            if (value) return {key: key, value: value};
                        }
                        return null;
                    }""")
                    
                    if token:
                        print(f"[BrowserLogin] ✅ 从 localStorage 提取到 token (key: {token['key']}, 长度: {len(token['value'])})", flush=True)
                        result["token"] = token["value"]
                    else:
                        print("[BrowserLogin] ⚠️ localStorage 中未找到 token", flush=True)
                except Exception as e:
                    print(f"[BrowserLogin] ⚠️ 从 localStorage 提取 token 失败: {e}", flush=True)
            
            # 获取cookies
            try:
                cookies = context.cookies()
                result["cookies"] = cookies
                print(f"[BrowserLogin] ✅ 获取到 {len(cookies)} 个 cookies", flush=True)
            except Exception as e:
                print(f"[BrowserLogin] ⚠️ 获取 cookies 失败: {e}", flush=True)
            
            result["success"] = True
            print("[BrowserLogin] ====== 浏览器登录成功 ======", flush=True)
            page.wait_for_timeout(1000)
            browser.close()
            return result
            
    except Exception as e:
        print(f"[BrowserLogin] ❌ 浏览器登录异常: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return result

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python browser_login_helper.py <username> <password> <login_url>")
        sys.exit(1)
    
    username = sys.argv[1]
    password = sys.argv[2]
    login_url = sys.argv[3]
    
    result = login_2925(username, password, login_url)
    
    # 输出JSON结果
    print(json.dumps(result, ensure_ascii=False))
