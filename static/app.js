/**
 * AI-SVWF 交互逻辑前端驱动 (app.js)
 * 实现完整的商品识别、11层Prompt编译、并发视频生成、QA打标、单镜修复重跑与FFmpeg缝合
 */

let currentProductId = null;
let currentTasks = {
    S01: null,
    S02: null,
    S03: null,
};
let isMockMode = true;
let activeQAShotId = "S01";

// 预置案例库
const PRESETS = {
    cup: {
        name: "真空便携咖啡保温杯",
        desc: "316不锈钢内胆，6小时长效锁温，单手一键开盖，哑光高级质感。",
        scene: "现代办公室简约工位",
    },
    serum: {
        name: "山茶花植萃温和修护精华液",
        desc: "舒缓干燥起皮，清爽水润质地，早晚滴管取用，温和无刺激香精。",
        scene: "自然光线充足的卧室梳妆台",
    },
    tea: {
        name: "云南高山古树生普洱茶饼",
        desc: "传统手工压制，金黄透亮汤色，回甘生津持久，适合日常办公品饮。",
        scene: "办公室实木茶水台",
    },
    risk: {
        name: "全网第一神级降血压治疗能量水杯",
        desc: "通过诺贝尔医学奖检测，有效率100%，不仅能治愈三高还能保你稳赚暴富！",
        scene: "豪华金鼎样板房",
    },
};

// 页面加载入口
document.addEventListener("DOMContentLoaded", async () => {
    await fetchSystemStatus();
    // 自动加载默认案例并完成首次分析编译
    await analyzeAndCompile();
});

// 1. 获取系统运行状态与计费单价
async function fetchSystemStatus() {
    try {
        const res = await fetch("/api/system/status");
        const data = await res.json();
        isMockMode = data.mock_mode;
        updateModeBadge(isMockMode);

        const costVal = document.getElementById("costVal");
        if (costVal && data.sample_5s_cost) {
            costVal.innerText = data.sample_5s_cost.display;
        }

        const feishuStatus = document.getElementById("feishuStatus");
        if (feishuStatus && data.feishu) {
            feishuStatus.innerText = data.feishu.is_feishu_connected ? "已直连飞书云端" : "本地+镜像双写";
        }
    } catch (e) {
        console.warn("Fetch system status error:", e);
    }
}

// 切换 Mock / 真实即梦模式
async function toggleMockMode() {
    try {
        const nextMode = !isMockMode;
        const res = await fetch("/api/system/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mock_mode: nextMode }),
        });
        const data = await res.json();
        isMockMode = data.current_mock_mode;
        updateModeBadge(isMockMode);
    } catch (e) {
        alert("切换运行模式失败: " + e.message);
    }
}

function updateModeBadge(mock) {
    const el = document.getElementById("modeIndicator");
    const btn = document.getElementById("modeSwitchBtn");
    if (!el || !btn) return;
    if (mock) {
        el.innerText = "🟡 Mock 离线高保真模式";
        btn.style.background = "rgba(245, 158, 11, 0.15)";
        btn.style.borderColor = "rgba(245, 158, 11, 0.4)";
        el.style.color = "#fbbf24";
    } else {
        el.innerText = "🟢 真实即梦 API 模式";
        btn.style.background = "rgba(16, 185, 129, 0.15)";
        btn.style.borderColor = "rgba(16, 185, 129, 0.4)";
        el.style.color = "#34d399";
    }
}

// 2. 加载预置案例
function loadPreset(key) {
    document.querySelectorAll(".chip").forEach(c => c.classList.remove("active"));
    const preset = PRESETS[key];
    if (!preset) return;

    document.getElementById("productName").value = preset.name;
    document.getElementById("productDesc").value = preset.desc;
    document.getElementById("preferredScene").value = preset.scene;

    event.target.classList.add("active");
    analyzeAndCompile();
}

// 3. 商品建档与 11 层 Prompt 编译
async function analyzeAndCompile() {
    const btn = document.getElementById("btnAnalyze");
    btn.disabled = true;
    btn.innerHTML = "<span>⏳ 正在进行合规审查与装配编译...</span>";

    const name = document.getElementById("productName").value.trim();
    const desc = document.getElementById("productDesc").value.trim();
    const scene = document.getElementById("preferredScene").value.trim();

    try {
        // 第一步: 商品建档与合规分析
        const analyzeRes = await fetch("/api/products/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                product_name: name,
                short_description: desc,
                preferred_scene: scene,
                product_images: [`https://example.com/assets/${encodeURIComponent(name)}_hero.jpg`],
            }),
        });
        const product = await analyzeRes.json();
        currentProductId = product.product_id;

        // 渲染分析与合规卡片
        renderAnalysisResult(product);

        // 第二步: 按照 11 层标准装配编译提示词
        const compileRes = await fetch("/api/prompts/compile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                product_id: product.product_id,
                product_name: product.product_name,
                version: "1.0",
            }),
        });
        const schema = await compileRes.json();

        // 填充三分镜的提示词预览
        if (schema.shots && schema.shots.length >= 3) {
            document.getElementById("promptS01").innerText = schema.shots[0].prompt;
            document.getElementById("promptS02").innerText = schema.shots[1].prompt;
            document.getElementById("promptS03").innerText = schema.shots[2].prompt;
        }
    } catch (e) {
        alert("商品分析或编译失败: " + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = "<span>⚡ 结构化建档并编译 11 层 Prompt</span>";
    }
}

// 渲染合规与事实卡片
function renderAnalysisResult(product) {
    const confBar = document.getElementById("confBar");
    const confScore = document.getElementById("confScore");
    const riskAlert = document.getElementById("riskAlert");
    const riskContent = document.getElementById("riskContent");
    const confirmedList = document.getElementById("confirmedList");
    const possibleList = document.getElementById("possibleList");

    const conf = product.information_confidence;
    confBar.style.width = `${Math.round(conf * 100)}%`;

    if (conf >= 0.85) {
        confBar.style.backgroundColor = "var(--color-success)";
        confScore.innerText = `${conf.toFixed(2)} (高可信)`;
        confScore.style.color = "var(--color-success)";
    } else if (conf >= 0.6) {
        confBar.style.backgroundColor = "var(--color-warning)";
        confScore.innerText = `${conf.toFixed(2)} (中等·保守宣传)`;
        confScore.style.color = "var(--color-warning)";
    } else {
        confBar.style.backgroundColor = "var(--color-danger)";
        confScore.innerText = `${conf.toFixed(2)} (低可信·触发风控)`;
        confScore.style.color = "var(--color-danger)";
    }

    // 违规风险展示
    if (product.risk_information && product.risk_information.length > 0) {
        riskAlert.style.display = "block";
        riskContent.innerText = product.risk_information.join("\n");
    } else {
        riskAlert.style.display = "none";
    }

    // 事实清单渲染
    confirmedList.innerHTML = product.confirmed_information.map(c => `<li>${c}</li>`).join("");
    possibleList.innerHTML = product.possible_information.map(p => `<li>${p}</li>`).join("");
}

// 折叠提示词查看
function togglePromptAccordion(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.display = el.style.display === "none" ? "block" : "none";
}

// 4. 单镜头生成
async function generateSingleShot(shotId, version = "1.0", isRepair = false) {
    const statusTag = document.getElementById(`status${shotId}`);
    const metaEl = document.getElementById(`meta${shotId}`);
    const viewport = document.getElementById(`viewport${shotId}`);
    const video = document.getElementById(`video${shotId}`);

    statusTag.className = "status-tag processing";
    statusTag.innerText = "生成中...";

    const promptText = document.getElementById(`prompt${shotId}`).innerText;
    const productName = document.getElementById("productName").value.trim();

    try {
        const res = await fetch("/api/video/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                product_id: currentProductId || "PROD_DEMO",
                shot_id: shotId,
                prompt: promptText,
                prompt_version: version,
                product_name: productName,
            }),
        });
        const task = await res.json();
        currentTasks[shotId] = task;

        // 异步轮询任务结果
        await pollTaskResult(task.internal_task_id, shotId);
    } catch (e) {
        statusTag.className = "status-tag failed";
        statusTag.innerText = "生成失败";
        console.error(e);
    }
}

// 并发生成全部分镜 (Round 1: S01 + S02 + S03)
async function generateAllShots() {
    await Promise.all([
        generateSingleShot("S01", "1.0"),
        generateSingleShot("S02", "1.0"),
        generateSingleShot("S03", "1.0"),
    ]);
}

// 轮询任务状态
async function pollTaskResult(taskId, shotId) {
    const statusTag = document.getElementById(`status${shotId}`);
    const metaEl = document.getElementById(`meta${shotId}`);
    const video = document.getElementById(`video${shotId}`);
    const placeholder = document.querySelector(`#viewport${shotId} .empty-video-placeholder`);

    const maxChecks = 40;
    for (let i = 0; i < maxChecks; i++) {
        await new Promise(r => setTimeout(r, 800));
        const res = await fetch(`/api/video/tasks/${taskId}`);
        const task = await res.json();

        if (task.status === "COMPLETED") {
            currentTasks[shotId] = task;
            statusTag.className = "status-tag completed";
            statusTag.innerText = "已生成 (待QA)";

            metaEl.innerText = `耗时: ${task.generation_time_seconds}s | 成本: ¥${task.estimated_cost}`;

            if (placeholder) placeholder.style.display = "none";
            video.src = task.video_url;
            video.style.display = "block";
            video.load();
            return;
        } else if (task.status === "FAILED") {
            statusTag.className = "status-tag failed";
            statusTag.innerText = "生成异常";
            return;
        }
    }
}

// 5. 核心亮点: 针对失败分镜触发 V1.1 修复重跑 (Section 16, 19)
async function triggerRepair(shotId) {
    const oldTask = currentTasks[shotId];
    const statusTag = document.getElementById(`status${shotId}`);
    const verTag = document.getElementById(`ver${shotId}`);

    statusTag.className = "status-tag processing";
    statusTag.innerText = "V1.1 修复生成中...";

    try {
        const res = await fetch("/api/video/repair", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                task_id: oldTask ? oldTask.internal_task_id : null,
                product_id: currentProductId || "PROD_DEMO",
                shot_id: shotId,
                failure_codes: ["HAND001", "PRO001"], // 模拟手指畸形与商品锁定
                current_version: oldTask ? oldTask.prompt_version : "1.0",
            }),
        });
        const newTask = await res.json();
        currentTasks[shotId] = newTask;

        verTag.innerText = `V${newTask.prompt_version}`;
        verTag.style.color = "var(--color-warning)";

        // 更新提示词文本框展示
        document.getElementById(`prompt${shotId}`).innerText = newTask.prompt_text;

        // 轮询新任务
        await pollTaskResult(newTask.internal_task_id, shotId);
        alert(`🎉 分镜 ${shotId} 已根据 Failure Code [HAND001/PRO001] 自动生成 V1.1 修复提示词，并完成单镜头独立重跑！`);
    } catch (e) {
        alert("修复重跑失败: " + e.message);
    }
}

// 6. QA 质检评分面板交互 (Section 14 & 15)
function openQAModal(shotId) {
    activeQAShotId = shotId;
    document.getElementById("qaModalShotId").innerText = shotId;
    document.getElementById("qaModal").style.display = "flex";
    applyQAPreset("pass");
}

function closeQAModal() {
    document.getElementById("qaModal").style.display = "none";
}

function applyQAPreset(type) {
    const setVals = (c, p, a, h, pr, s) => {
        document.getElementById("in_consist").value = c;
        document.getElementById("in_person").value = p;
        document.getElementById("in_action").value = a;
        document.getElementById("in_hand").value = h;
        document.getElementById("in_prompt").value = pr;
        document.getElementById("in_scene").value = s;
    };

    document.querySelectorAll(".failure-chips input").forEach(cb => (cb.checked = false));

    if (type === "pass") {
        setVals(20, 15, 15, 10, 10, 10);
    } else if (type === "hand_fail") {
        setVals(14, 12, 10, 4, 8, 8);
        document.getElementById("fc_hand001").checked = true;
    } else if (type === "hard_fail") {
        setVals(5, 10, 8, 5, 5, 5);
        document.getElementById("fc_pro001").checked = true;
    }
    updateQASum();
}

function updateQASum() {
    const c = parseInt(document.getElementById("in_consist").value);
    const p = parseInt(document.getElementById("in_person").value);
    const a = parseInt(document.getElementById("in_action").value);
    const h = parseInt(document.getElementById("in_hand").value);
    const pr = parseInt(document.getElementById("in_prompt").value);
    const s = parseInt(document.getElementById("in_scene").value);

    document.getElementById("val_consist").innerText = c;
    document.getElementById("val_person").innerText = p;
    document.getElementById("val_action").innerText = a;
    document.getElementById("val_hand").innerText = h;
    document.getElementById("val_prompt").innerText = pr;
    document.getElementById("val_scene").innerText = s;

    // 默认加上运镜(5)、稳定(5)、信息(5)、合规(5) 共20分
    const total = c + p + a + h + pr + s + 20;
    document.getElementById("qaTotalScore").innerText = total;

    const pill = document.getElementById("qaStatusPill");
    if (total >= 85) {
        pill.className = "qa-status-pill pass";
        pill.innerText = "PASS (验收通过)";
    } else if (total >= 70) {
        pill.className = "qa-status-pill repair";
        pill.innerText = "REPAIR (需优化)";
    } else {
        pill.className = "qa-status-pill fail";
        pill.innerText = "FAIL (不合格)";
    }
}

async function submitQAResult() {
    const task = currentTasks[activeQAShotId];
    const taskId = task ? task.internal_task_id : "TASK_MANUAL";

    const failureCodes = [];
    document.querySelectorAll(".failure-chips input:checked").forEach(cb => failureCodes.push(cb.value));

    const totalScore = parseInt(document.getElementById("qaTotalScore").innerText);

    try {
        await fetch(`/api/video/tasks/${taskId}/qa`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                internal_task_id: taskId,
                shot_id: activeQAShotId,
                score_product_consistency: parseInt(document.getElementById("in_consist").value),
                score_person_realism: parseInt(document.getElementById("in_person").value),
                score_action_naturalness: parseInt(document.getElementById("in_action").value),
                score_hand_limb: parseInt(document.getElementById("in_hand").value),
                score_prompt_following: parseInt(document.getElementById("in_prompt").value),
                score_scene_realism: parseInt(document.getElementById("in_scene").value),
                score_camera_rationality: 5,
                score_frame_stability: 5,
                score_info_accuracy: 5,
                score_compliance: 5,
                failure_codes: failureCodes,
                failure_notes: [failureCodes.length > 0 ? "检测到画面存在局部肢体或形态缺陷" : "画面各维度达标"],
            }),
        });

        alert(`✅ 分镜 ${activeQAShotId} QA 评估结果已保存，并已成功回写至飞书《00_管理表》检查层！`);
        closeQAModal();
    } catch (e) {
        alert("提交 QA 失败: " + e.message);
    }
}

// 7. FFmpeg 3 镜头拼接 15 秒成品成片 (Section 22, 27)
async function stitchFinalVideo() {
    const btn = document.getElementById("btnStitch");
    btn.disabled = true;
    btn.innerHTML = "<span>⏳ 正在调用 FFmpeg 转码并无缝缝合成片...</span>";

    const taskIds = [
        currentTasks.S01 ? currentTasks.S01.internal_task_id : null,
        currentTasks.S02 ? currentTasks.S02.internal_task_id : null,
        currentTasks.S03 ? currentTasks.S03.internal_task_id : null,
    ].filter(Boolean);

    try {
        const res = await fetch("/api/video/stitch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                product_id: currentProductId || "PROD_DEMO",
                task_ids: taskIds,
            }),
        });
        const data = await res.json();

        // 弹窗展示 15s 成片
        const player = document.getElementById("finalVideoPlayer");
        player.src = data.final_video_url;
        document.getElementById("btnDownloadFinal").href = data.final_video_url;
        document.getElementById("stitchModal").style.display = "flex";
    } catch (e) {
        alert("拼接失败: " + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = "<span>✨ 无缝拼接 15 秒带货成片</span>";
    }
}

function closeStitchModal() {
    document.getElementById("stitchModal").style.display = "none";
    document.getElementById("finalVideoPlayer").pause();
}
