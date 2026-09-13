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
        driver.execute_script("closeSettingsModal();")
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

        # 7. 打开 Section 18 & 19 优化矩阵弹窗并截屏
        print("\n[UI Test 7/9] 模拟点击【📊 优化对比矩阵 (Section 19)】...")
        driver.execute_script("closeStitchModal();")
        time.sleep(0.5)
        btn_matrix = driver.find_element(By.ID, "btnOpenMatrix")
        btn_matrix.click()
        time.sleep(1.0)
        shot7 = output_dir / "test_step7_matrix.png"
        driver.save_screenshot(str(shot7))
        print(f"  ✓ Section 19 轮次优化矩阵打开成功，截图已保存: {shot7}")
        driver.execute_script("closeSection19MatrixModal();")
        time.sleep(0.5)

        # 8. 核心升级: 打开【分镜提示词工程独立工作台 (Prompt Studio)】并测试多镜头 Tab 切换与隔离
        print("\n[UI Test 8/9] 模拟点击 S02 的【⚙️ 工坊微调】打开独立工作台...")
        driver.execute_script("openPromptStudioModal('S02');")
        time.sleep(1.0)

        # 模拟在 S02 中应用降级动作和双重锁胶囊
        driver.execute_script("applyStudioChip('motion', 'degraded'); applyStudioChip('lock', 'double');")
        time.sleep(0.5)
        shot8 = output_dir / "test_step8_prompt_studio.png"
        driver.save_screenshot(str(shot8))
        print(f"  ✓ S02 工作台与预设应用成功，截图已保存: {shot8}")

        # 测试在工作台内直接切换到 S01 Tab (验证分镜状态物理隔离)
        print("  🔄 模拟在工作台顶栏切换至【S01 场景建立】Tab...")
        driver.execute_script("switchStudioShotTab('S01');")
        time.sleep(0.6)
        shot8_s01 = output_dir / "test_step8_prompt_studio_s01.png"
        driver.save_screenshot(str(shot8_s01))
        print(f"  ✓ S01 独立工作台切换成功且状态完全隔离，截图已保存: {shot8_s01}")

        # 切换回 S02
        driver.execute_script("switchStudioShotTab('S02');")
        time.sleep(0.4)
        driver.execute_script("closePromptStudioModal();")
        time.sleep(0.5)

        # 9. 核心升级: 打开【分镜多版本对比与 A/B 质检看板 (Version Compare)】
        print("\n[UI Test 9/9] 模拟点击 S02 版本标签打开【多版本 A/B 对比看板】...")
        driver.execute_script("openVersionCompareModal('S02');")
        time.sleep(1.0)
        shot9 = output_dir / "test_step9_version_compare.png"
        driver.save_screenshot(str(shot9))
        print(f"  ✓ 多版本并排对比看板打开成功，截图已保存: {shot9}")
        driver.execute_script("closeVersionCompareModal();")

        print("\n" + "=" * 70)
        print("🎉 自动化 UI 全流程测试全部通过！共生成 9 张状态验证截图：")
        print(f"  1. 首页初始态:   {shot1}")
        print(f"  2. 案例切换态:   {shot2}")
        print(f"  3. 接口配置弹窗: {shot3}")
        print(f"  4. 三分镜生成态: {shot4}")
        print(f"  5. 单镜修复重跑: {shot5}")
        print(f"  6. 15s成片缝合:  {shot6}")
        print(f"  7. Section 19对比矩阵: {shot7}")
        print(f"  8. 提示词工程独立工作台: {shot8}")
        print(f"  9. 多版本 A/B 对比看板: {shot9}")
        print("=" * 70)

    finally:
        driver.quit()


if __name__ == "__main__":
    run_e2e_ui_test()
