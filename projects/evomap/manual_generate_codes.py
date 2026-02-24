"""
手动登录指定账号并生成邀请码
用法: python manual_generate_codes.py <email> <password>
"""
import sys
import json
from register import (
    create_browser, create_context, _login_and_generate_codes,
    load_state, save_state, append_invite_codes, log
)
from playwright.sync_api import sync_playwright

def main():
    if len(sys.argv) < 3:
        print("用法: python manual_generate_codes.py <email> <password>")
        sys.exit(1)

    email = sys.argv[1]
    password = sys.argv[2]

    log(f"登录账号: {email}")

    with sync_playwright() as pw:
        browser = create_browser(pw, headless=False)
        context, page = create_context(browser)

        try:
            # 登录并生成邀请码
            codes = _login_and_generate_codes(page, context, email, password)

            if codes and len(codes) >= 3:
                log(f"成功生成 {len(codes)} 个邀请码: {codes}")

                # 更新 state
                state = load_state()
                state["invite_pool"].append(codes[0])
                state["output_codes"].extend(codes[1:3])
                save_state(state)

                # 保存到文件
                append_invite_codes(codes[1:3])

                log(f"邀请码已分配: 回池={codes[0]}, 输出={codes[1:3]}")
                log(f"当前 invite_pool: {len(state['invite_pool'])} 个")
            else:
                log(f"生成邀请码失败或数量不足: {codes}", "ERROR")

        except Exception as e:
            log(f"错误: {e}", "ERROR")
        finally:
            browser.close()

if __name__ == "__main__":
    main()
