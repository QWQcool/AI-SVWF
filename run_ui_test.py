"""Selenium visual regression with state assertions (no fixed success sleeps)."""

import sys
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


BASE_URL = "http://127.0.0.1:8000"


def run_e2e_ui_test() -> None:
    output_dir = Path(__file__).resolve().parent / "outputs"
    output_dir.mkdir(exist_ok=True)
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1600,960")
    opts.set_capability("ms:loggingPrefs", {"browser": "ALL"})
    driver = webdriver.Edge(options=opts)
    wait = WebDriverWait(driver, 90)

    def snap(name: str) -> None:
        path = output_dir / name
        assert driver.save_screenshot(str(path)), f"截图失败: {path}"
        assert path.exists() and path.stat().st_size > 1000
        print(f"  ✓ {path.name}")

    try:
        print("[1/9] 首页与自动建档")
        driver.get(BASE_URL)
        wait.until(EC.presence_of_element_located((By.ID, "btnAnalyze")))
        wait.until(lambda d: d.find_element(By.ID, "promptS01").get_attribute("value"))
        snap("test_step1_home.png")

        print("[2/9] 切换预置商品")
        serum = driver.find_elements(By.CLASS_NAME, "chip")[1]
        serum.click()
        wait.until(lambda d: "精华液" in d.find_element(By.ID, "productName").get_attribute("value"))
        wait.until(lambda d: "精华液" in d.find_element(By.ID, "promptS02").get_attribute("value"))
        snap("test_step2_preset.png")

        print("[3/9] 设置弹窗（密钥不回显）")
        driver.find_element(By.ID, "btnOpenSettings").click()
        wait.until(EC.visibility_of_element_located((By.ID, "settingsModal")))
        assert driver.find_element(By.ID, "cfg_jimeng_key").get_attribute("value") == ""
        assert driver.find_element(By.ID, "cfg_llm_key").get_attribute("value") == ""
        snap("test_step3_settings_modal.png")
        driver.execute_script("closeSettingsModal()")

        print("[4/9] 三分镜真实任务状态")
        driver.find_element(By.XPATH, "//button[contains(., '一键并发生成全部分镜')]").click()
        for shot in ("S01", "S02", "S03"):
            wait.until(lambda d, s=shot: "已生成" in d.find_element(By.ID, f"status{s}").text)
            wait.until(lambda d, s=shot: bool(d.find_element(By.ID, f"video{s}").get_attribute("src")))
        snap("test_step4_generated.png")

        print("[5/9] 精确点击 S02 单镜头修复")
        driver.find_element(By.CSS_SELECTOR, "#cardS02 .btn-repair").click()
        wait.until(lambda d: d.find_element(By.ID, "verS02").text == "V1.1")
        wait.until(lambda d: "已生成" in d.find_element(By.ID, "statusS02").text)
        assert driver.find_element(By.ID, "verS01").text == "V1.0"
        assert driver.find_element(By.ID, "verS03").text == "V1.0"
        snap("test_step5_repaired.png")

        print("[6/9] Mock 预览拼接（显式非 QA 结论）")
        driver.find_element(By.ID, "btnStitch").click()
        wait.until(EC.visibility_of_element_located((By.ID, "stitchModal")))
        wait.until(lambda d: bool(d.find_element(By.ID, "finalVideoPlayer").get_attribute("src")))
        snap("test_step6_stitched.png")

        print("[7/9] SQLite 实际矩阵")
        driver.execute_script("closeStitchModal()")
        driver.find_element(By.ID, "btnOpenMatrix").click()
        wait.until(EC.visibility_of_element_located((By.ID, "section19Modal")))
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "#matrixTableBody tr")) >= 3)
        snap("test_step7_matrix.png")
        driver.execute_script("closeSection19MatrixModal()")

        print("[8/9] Prompt Studio 分镜隔离")
        driver.execute_script("openPromptStudioModal('S02')")
        wait.until(EC.visibility_of_element_located((By.ID, "promptStudioModal")))
        driver.execute_script("applyStudioChip('motion', 'degraded'); applyStudioChip('lock', 'double');")
        snap("test_step8_prompt_studio.png")
        driver.execute_script("switchStudioShotTab('S01')")
        wait.until(lambda d: "S01" in d.find_element(By.ID, "studioShotBadge").text)
        snap("test_step8_prompt_studio_s01.png")
        driver.execute_script("closePromptStudioModal()")

        print("[9/9] 版本对比不请求虚构演示文件")
        driver.execute_script("openVersionCompareModal('S02')")
        wait.until(EC.visibility_of_element_located((By.ID, "versionCompareModal")))
        baseline_src = driver.find_element(By.ID, "cmpVideoV10").get_attribute("src")
        assert "S02_V1.0_demo.mp4" not in (baseline_src or "")
        snap("test_step9_version_compare.png")

        severe_logs = [
            entry for entry in driver.get_log("browser")
            if entry.get("level") == "SEVERE" and "favicon.ico" not in entry.get("message", "")
        ]
        assert not severe_logs, f"浏览器出现严重错误: {severe_logs}"
        print("UI E2E 通过：所有截图前均验证了对应页面状态。")
    finally:
        driver.quit()


if __name__ == "__main__":
    run_e2e_ui_test()
