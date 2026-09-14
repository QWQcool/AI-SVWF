"""Selenium visual regression with state assertions (no fixed success sleeps)."""

import sys
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.keys import Keys
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
        print("[1/10] 首页与自动建档")
        driver.get(BASE_URL)
        wait.until(EC.presence_of_element_located((By.ID, "btnAnalyze")))
        # UI 回归只能跑离线 Mock；即使服务进程此前被手工切到真实模式，也绝不触发付费任务。
        if not driver.execute_script("return isMockMode"):
            mock_result = driver.execute_async_script(
                "const done = arguments[arguments.length - 1];"
                "fetch('/api/system/settings', {method: 'POST', headers: {'Content-Type': 'application/json'},"
                "body: JSON.stringify({mock_mode: true})})"
                ".then(r => r.json()).then(done).catch(e => done({error: e.message}));"
            )
            assert mock_result.get("current_mock_mode") is True, mock_result
            driver.refresh()
            wait.until(EC.presence_of_element_located((By.ID, "btnAnalyze")))
            wait.until(lambda d: d.execute_script("return isMockMode") is True)
        wait.until(lambda d: d.find_element(By.ID, "promptS01").get_attribute("value"))
        snap("test_step1_home.png")

        print("[2/10] 切换预置商品")
        serum = driver.find_elements(By.CLASS_NAME, "chip")[1]
        serum.click()
        wait.until(lambda d: "精华液" in d.find_element(By.ID, "productName").get_attribute("value"))
        wait.until(lambda d: "精华液" in d.find_element(By.ID, "promptS02").get_attribute("value"))
        snap("test_step2_preset.png")

        print("[3/10] 设置弹窗（密钥不回显）")
        driver.find_element(By.ID, "btnOpenSettings").click()
        wait.until(EC.visibility_of_element_located((By.ID, "settingsModal")))
        assert driver.find_element(By.ID, "cfg_jimeng_key").get_attribute("value") == ""
        assert driver.find_element(By.ID, "cfg_llm_key").get_attribute("value") == ""
        assert driver.find_element(By.ID, "cfg_feishu_token").get_attribute("value") == ""
        assert driver.find_element(By.ID, "cfg_feishu_token").get_attribute("type") == "password"
        close_button = driver.find_element(By.ID, "btnCloseSettings")
        close_rect = driver.execute_script("return arguments[0].getBoundingClientRect()", close_button)
        viewport_height = driver.execute_script("return window.innerHeight")
        assert close_rect["top"] >= 0 and close_rect["bottom"] <= viewport_height, (
            f"设置关闭按钮超出视口: {close_rect}, viewport={viewport_height}"
        )
        assert driver.find_element(By.ID, "btnCancelSettings").is_displayed()
        snap("test_step3_settings_modal.png")

        # 有未保存修改时，Esc 必须先询问；取消后弹窗与输入值均保留。
        cost_input = driver.find_element(By.ID, "cfg_cost_per_second")
        original_cost = cost_input.get_attribute("value")
        cost_input.clear()
        cost_input.send_keys("0.07" if original_cost != "0.07" else "0.08")
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        discard_prompt = wait.until(EC.alert_is_present())
        assert "尚未保存" in discard_prompt.text
        discard_prompt.dismiss()
        assert driver.find_element(By.ID, "settingsModal").is_displayed()
        assert cost_input.get_attribute("value") != original_cost

        # 再次按 Esc 并确认，可明确放弃修改并关闭。
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        wait.until(EC.alert_is_present()).accept()
        wait.until(EC.invisibility_of_element_located((By.ID, "settingsModal")))

        # 干净状态下，点击遮罩与显式“关闭”按钮都能直接退出。
        driver.find_element(By.ID, "btnOpenSettings").click()
        wait.until(EC.visibility_of_element_located((By.ID, "settingsModal")))
        driver.execute_script("document.getElementById('settingsModal').click()")
        wait.until(EC.invisibility_of_element_located((By.ID, "settingsModal")))
        driver.find_element(By.ID, "btnOpenSettings").click()
        wait.until(EC.visibility_of_element_located((By.ID, "settingsModal")))
        driver.find_element(By.ID, "btnCloseSettings").click()
        wait.until(EC.invisibility_of_element_located((By.ID, "settingsModal")))

        print("[4/10] 三分镜 Mock 预览任务状态")
        driver.find_element(By.XPATH, "//button[contains(., '一键生成三分镜预览')]").click()
        for shot in ("S01", "S02", "S03"):
            wait.until(lambda d, s=shot: "已生成" in d.find_element(By.ID, f"status{s}").text)
            wait.until(lambda d, s=shot: bool(d.find_element(By.ID, f"video{s}").get_attribute("src")))
        snap("test_step4_generated.png")

        print("[5/10] 人工 QA 后精确点击 S02 单镜头修复")
        driver.execute_script("openQAModal('S02'); applyQAPreset('hand_fail'); submitQAResult();")
        wait.until(EC.invisibility_of_element_located((By.ID, "qaModal")))
        driver.find_element(By.CSS_SELECTOR, "#cardS02 .btn-repair").click()
        wait.until(lambda d: d.find_element(By.ID, "verS02").text == "V1.1")
        wait.until(lambda d: "已生成" in d.find_element(By.ID, "statusS02").text)
        assert driver.find_element(By.ID, "verS01").text == "V1.0"
        assert driver.find_element(By.ID, "verS03").text == "V1.0"
        snap("test_step5_repaired.png")

        print("[6/10] Mock 预览拼接（显式非 QA 结论）")
        driver.execute_script("document.getElementById('chkEnableTts').checked = false;")
        driver.find_element(By.ID, "btnStitch").click()
        wait.until(EC.visibility_of_element_located((By.ID, "stitchModal")))
        wait.until(lambda d: bool(d.find_element(By.ID, "finalVideoPlayer").get_attribute("src")))
        snap("test_step6_stitched.png")

        print("[7/10] SQLite 实际矩阵")
        driver.execute_script("closeStitchModal()")
        driver.find_element(By.ID, "btnOpenMatrix").click()
        wait.until(EC.visibility_of_element_located((By.ID, "section19Modal")))
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "#matrixTableBody tr")) >= 3)
        snap("test_step7_matrix.png")
        driver.execute_script("closeSection19MatrixModal()")

        print("[8/10] Prompt Studio 分镜隔离")
        driver.execute_script("openPromptStudioModal('S02')")
        wait.until(EC.visibility_of_element_located((By.ID, "promptStudioModal")))
        driver.execute_script("applyStudioChip('motion', 'degraded'); applyStudioChip('lock', 'double');")
        snap("test_step8_prompt_studio.png")
        driver.execute_script("switchStudioShotTab('S01')")
        wait.until(lambda d: "S01" in d.find_element(By.ID, "studioShotBadge").text)
        snap("test_step8_prompt_studio_s01.png")
        driver.execute_script("closePromptStudioModal()")

        print("[9/10] 版本对比不请求虚构演示文件")
        driver.execute_script("openVersionCompareModal('S02')")
        wait.until(EC.visibility_of_element_located((By.ID, "versionCompareModal")))
        baseline_src = driver.find_element(By.ID, "cmpVideoV10").get_attribute("src")
        assert "S02_V1.0_demo.mp4" not in (baseline_src or "")
        snap("test_step9_version_compare.png")
        driver.execute_script("closeVersionCompareModal()")

        print("[10/10] 刷新恢复同一商品与任务（不重复提交）")
        before_product_id = driver.execute_script(
            "return localStorage.getItem('ai_svwf_current_product_id')"
        )
        before_task_ids = driver.execute_script(
            "return Object.fromEntries(Object.entries(currentTasks).map(([shot, task]) => "
            "[shot, task && task.internal_task_id]));"
        )
        assert before_product_id
        assert all(before_task_ids.values())
        driver.refresh()
        wait.until(
            lambda d: d.execute_script(
                "return localStorage.getItem('ai_svwf_current_product_id')"
            ) == before_product_id
        )
        wait.until(
            lambda d: all(
                "已生成" in d.find_element(By.ID, f"status{shot}").text
                for shot in ("S01", "S02", "S03")
            )
        )
        for shot in ("S01", "S02", "S03"):
            assert driver.find_element(By.ID, f"video{shot}").get_attribute("src"), (
                f"刷新后 {shot} 视频未恢复"
            )
        assert driver.find_element(By.ID, "verS02").text == "V1.1"
        assert "版本: V1.1" in driver.find_element(By.ID, "promptS02").get_attribute("value")
        after_task_ids = driver.execute_script(
            "return Object.fromEntries(Object.entries(currentTasks).map(([shot, task]) => "
            "[shot, task && task.internal_task_id]));"
        )
        assert after_task_ids == before_task_ids, (
            f"刷新后任务发生变化: before={before_task_ids}, after={after_task_ids}"
        )
        snap("test_step10_restored.png")

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
