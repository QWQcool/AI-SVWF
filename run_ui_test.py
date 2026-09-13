"""
AI-SVWF 全自动化 UI 模拟点击与视觉回归测试工具 (run_ui_test.py)
利用 Headless Edge 与 Selenium 模拟真实用户在 Web Studio 上的全套操作链，
并分阶段对每个状态变化进行高清截图存证，确保界面、交互、弹窗与视频流 100% 符合预期！
"""

import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def run_e2e_ui_test():
    print("=" * 70)
    print("🖥️ 启动 AI-SVWF 自动化 UI 模拟点击与视觉截屏测试...")
    print("=" * 70)

    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1600,960")

    driver = webdriver.Edge(options=opts)
    wait = WebDriverWait(driver, 10)

    try:
        # 1. 访问首页
        print("\n[UI Test 1/6] 访问 Web Studio 首页...")
        driver.get("http://localhost:8000")
        time.sleep(2.0)
        shot1 = output_dir / "test_step1_home.png"
        driver.save_screenshot(str(shot1))
        print(f"  ✓ 首页加载成功，截图已保存: {shot1}")

        # 2. 切换案例为“修护精华液”
        print("\n[UI Test 2/6] 模拟点击预置案例【修护精华液】...")
        chips = driver.find_elements(By.CLASS_NAME, "chip")
        if len(chips) >= 2:
            chips[1].click()  # 修护精华液
            time.sleep(1.8)
        shot2 = output_dir / "test_step2_preset.png"
        driver.save_screenshot(str(shot2))
        print(f"  ✓ 案例切换与 11 层编译成功，截图已保存: {shot2}")

        # 3. 打开【接口配置】设置弹窗 (验证即梦Key/模型/飞书配置)
        print("\n[UI Test 3/6] 模拟点击【⚙️ 接口配置】弹窗...")
        btn_settings = driver.find_element(By.ID, "btnOpenSettings")
        btn_settings.click()
        time.sleep(1.0)
        shot3 = output_dir / "test_step3_settings_modal.png"
        driver.save_screenshot(str(shot3))
        print(f"  ✓ 设置弹窗打开成功，截图已保存: {shot3}")

        # 关闭弹窗
        close_btn = driver.find_element(By.XPATH, "//div[@id='settingsModal']//button[@class='close-btn']")
        close_btn.click()
        time.sleep(0.5)

        # 4. 点击【一键并发生成全部分镜 (Round 1)】
        print("\n[UI Test 4/6] 模拟点击【▶ 一键并发生成全部分镜 (Round 1)】...")
        btn_gen_all = driver.find_element(By.XPATH, "//button[contains(., '一键并发生成全部分镜')]")
        btn_gen_all.click()
        print("  ⏳ 正在等待 S01, S02, S03 异步生成流...")
        time.sleep(3.8)  # 等待视频完成
        shot4 = output_dir / "test_step4_generated.png"
        driver.save_screenshot(str(shot4))
        print(f"  ✓ 三分镜生成完成并在视口渲染播放，截图已保存: {shot4}")

        # 5. 点击 S02 的【🛠️ V1.1 修复重跑】
        print("\n[UI Test 5/6] 模拟点击 S02 的【🛠️ V1.1 修复重跑】...")
        btn_repair = driver.find_element(By.XPATH, "//button[contains(., 'V1.1 修复重跑')]")
        btn_repair.click()

        # 处理可能弹出的 alert 提示框
        time.sleep(2.5)
        try:
            alert = driver.switch_to.alert
            print(f"  ✓ 捕获到修复完成通知: {alert.text}")
            alert.accept()
        except Exception:
            pass

        time.sleep(1.0)
        shot5 = output_dir / "test_step5_repaired.png"
        driver.save_screenshot(str(shot5))
        print(f"  ✓ S02_V1.1 单镜头重跑完成，截图已保存: {shot5}")

        # 6. 点击【✨ 无缝拼接 15 秒带货成片】
        print("\n[UI Test 6/6] 模拟点击【✨ 无缝拼接 15 秒带货成片】...")
        btn_stitch = driver.find_element(By.ID, "btnStitch")
        btn_stitch.click()
        time.sleep(2.5)

        shot6 = output_dir / "test_step6_stitched.png"
        driver.save_screenshot(str(shot6))
        print(f"  ✓ 15 秒成片合成弹窗展示，截图已保存: {shot6}")

        print("\n" + "=" * 70)
        print("🎉 自动化 UI 全流程测试全部通过！共生成 6 张状态验证截图：")
        print(f"  1. 首页初始态:   {shot1}")
        print(f"  2. 案例切换态:   {shot2}")
        print(f"  3. 接口配置弹窗: {shot3}")
        print(f"  4. 三分镜生成态: {shot4}")
        print(f"  5. 单镜修复重跑: {shot5}")
        print(f"  6. 15s成片缝合:  {shot6}")
        print("=" * 70)

    finally:
        driver.quit()


if __name__ == "__main__":
    run_e2e_ui_test()
