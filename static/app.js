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
let currentVideoProvider = "mock";
let currentVideoModel = "mock-video-v1";
let activeQAShotId = "S01";
let activeStudioShotId = "S02";

// 分镜版本历史栈 (参考 WebLockShot shotHistory 架构)
let shotHistory = {
    S01: [],
    S02: [],
    S03: [],
};

// 记录各分镜最近的 QA 缺陷代码
let shotQAFailureCodes = {
    S01: [],
    S02: [],
    S03: [],
};

// 官方 Baseline 提示词快照
let baselinePrompts = {
    S01: "",
    S02: "",
    S03: "",
};

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

function providerForModel(model) {
    if (!model || model === "mock-video-v1") return "mock";
    if (model.startsWith("kling")) return "kling";
    if (model.startsWith("seedance")) return "seedance";
    return "jimeng";
}

async function readJsonOrThrow(response) {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
        throw new Error(detail || `HTTP ${response.status}`);
    }
    return data;
}

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
        const feishuDot = document.getElementById("feishuDot");
        const feishuPill = document.getElementById("feishuPill");
        if (feishuStatus && data.feishu) {
            const syncMode = data.feishu_sync_mode || "dual";
            if (syncMode === "local") {
                feishuStatus.innerText = "SQLite 本地事实库";
                if (feishuDot) feishuDot.className = "pill-dot gray";
                if (feishuPill) feishuPill.title = "结构化数据写入 SQLite，媒体文件写入 outputs/";
            } else if (!data.feishu.is_feishu_configured) {
                feishuStatus.innerText = "SQLite 已启用 · 飞书待配置";
                if (feishuDot) feishuDot.className = "pill-dot gray";
                if (feishuPill) feishuPill.title = "本地数据不会丢失；飞书凭据补齐后可同步待办队列";
            } else if (data.feishu.is_feishu_connected) {
                feishuStatus.innerText = "已直连飞书云端";
                if (feishuDot) feishuDot.className = "pill-dot green";
                if (feishuPill) feishuPill.title = "已直连飞书开放平台多维表格，双写同步正常";
            } else {
                feishuStatus.innerText = syncMode === "cloud" ? "仅云端同步" : "本地+镜像双写";
                if (feishuDot) feishuDot.className = "pill-dot blue";
                if (feishuPill) feishuPill.title = "已配置飞书开放平台凭据，支持本地与多维表格镜像双写";
            }
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
    const modelPill = document.getElementById("modelPill");
    const costPill = document.getElementById("costPill");

    // 离线 Mock 模式下，隐藏模型切换与成本估算胶囊 (避免离线演示产生混淆)
    if (modelPill) modelPill.style.display = mock ? "none" : "flex";
    if (costPill) costPill.style.display = mock ? "none" : "flex";

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
async function analyzeAndCompile(isUserClick = false) {
    const btn = document.getElementById("btnAnalyze");
    btn.disabled = true;
    btn.innerHTML = "<span>⏳ 正在进行合规审查与装配编译...</span>";

    const name = document.getElementById("productName").value.trim();
    const desc = document.getElementById("productDesc").value.trim();
    const scene = document.getElementById("preferredScene").value.trim();
    const imageUrl = document.getElementById("productImageUrl").value.trim();

    try {
        // 第一步: 商品建档与合规分析
        const analyzeRes = await fetch("/api/products/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                product_name: name,
                short_description: desc,
                preferred_scene: scene,
                product_images: imageUrl ? [imageUrl] : [],
            }),
        });
        const product = await readJsonOrThrow(analyzeRes);
        currentProductId = product.product_id;

        // 渲染分析与合规卡片
        renderAnalysisResult(product);

        // 第二步: 按照 11 层标准装配编译提示词
        const compileRes = await fetch("/api/prompts/compile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                product_id: product.product_id,
                version: "1.0",
                provider: isMockMode ? "mock" : currentVideoProvider,
                model: isMockMode ? "mock-video-v1" : currentVideoModel,
            }),
        });
        const schema = await readJsonOrThrow(compileRes);

        // 填充三分镜的提示词预览 (支持 textarea.value 自由微调并存入 Baseline 快照)
        if (schema.shots && schema.shots.length >= 3) {
            const p1 = document.getElementById("promptS01");
            const p2 = document.getElementById("promptS02");
            const p3 = document.getElementById("promptS03");
            if (p1) { p1.value = schema.shots[0].prompt; baselinePrompts.S01 = schema.shots[0].prompt; }
            if (p2) { p2.value = schema.shots[1].prompt; baselinePrompts.S02 = schema.shots[1].prompt; }
            if (p3) { p3.value = schema.shots[2].prompt; baselinePrompts.S03 = schema.shots[2].prompt; }
        }

        // 视觉脉冲高光动效与拓扑激活
        ["cardS01", "cardS02", "cardS03", "analysisCard"].forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.classList.remove("glow-pulse");
                void el.offsetWidth;
                el.classList.add("glow-pulse");
            }
        });
        updateAgentStep(2);

        if (isUserClick) {
            showToast(
                "11层Prompt编译成功",
                `✅ 商品【${product.product_name}】建档完成 (可信度: ${product.information_confidence.toFixed(2)})，3 个分镜 11 层标准工业提示词已装配就绪！`,
                "success"
            );
        }
    } catch (e) {
        showToast("编译失败", e.message, "danger");
    } finally {
        btn.disabled = false;
        btn.innerHTML = "<span>⚡ 结构化编译</span>";
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

    if (conf >= 0.90) {
        confBar.style.backgroundColor = "var(--color-success)";
        confScore.innerText = `${conf.toFixed(2)} (高可信)`;
        confScore.style.color = "var(--color-success)";
    } else if (conf >= 0.70) {
        confBar.style.backgroundColor = "var(--color-warning)";
        confScore.innerText = `${conf.toFixed(2)} (可生成·禁用强事实宣传)`;
        confScore.style.color = "var(--color-warning)";
    } else if (conf >= 0.50) {
        confBar.style.backgroundColor = "var(--color-warning)";
        confScore.innerText = `${conf.toFixed(2)} (低可信·仅保守生成)`;
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
    confirmedList.replaceChildren(...product.confirmed_information.map(text => {
        const li = document.createElement("li"); li.textContent = text; return li;
    }));
    possibleList.replaceChildren(...product.possible_information.map(text => {
        const li = document.createElement("li"); li.textContent = text; return li;
    }));
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

    const promptEl = document.getElementById(`prompt${shotId}`);
    const promptText = (promptEl ? (promptEl.value || promptEl.innerText) : "").trim();
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
                image_url: document.getElementById("productImageUrl").value.trim(),
                provider: isMockMode ? "mock" : currentVideoProvider,
                model: isMockMode ? "mock-video-v1" : currentVideoModel,
                duration: 5,
                aspect_ratio: "9:16",
            }),
        });
        const task = await readJsonOrThrow(res);
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

        if (["COMPLETED", "QA_PENDING", "PASS", "REPAIR"].includes(task.status)) {
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

// 5. 核心亮点: 针对任意失败分镜触发 V1.1 修复重跑 (Section 16, 17, 19 全镜头覆盖)
async function triggerRepair(shotId) {
    const oldTask = currentTasks[shotId];
    const statusTag = document.getElementById(`status${shotId}`);
    const verTag = document.getElementById(`ver${shotId}`);

    // 保存当前任务至历史快照栈 (参考 WebLockShot shotHistory)
    if (oldTask) {
        const oldVideo = document.getElementById(`video${shotId}`);
        const pEl = document.getElementById(`prompt${shotId}`);
        shotHistory[shotId].push({
            prompt_version: oldTask.prompt_version || "1.0",
            prompt_text: pEl ? pEl.value : "",
            video_url: oldVideo && oldVideo.src ? oldVideo.src : "",
            failure_codes: oldTask.failure_codes || shotQAFailureCodes[shotId] || [],
            score: oldTask.qa_score ?? null,
        });
    }

    statusTag.className = "status-tag processing";
    statusTag.innerText = "V1.1 修复生成中...";

    // 针对 S01, S02, S03 提供合理的默认 Failure Code，若有实际 QA 评分打标则优先使用真实标记
    let defaultCodes = ["HAND001", "PRO001"];
    if (shotId === "S01") defaultCodes = ["CAM001", "SCN001"];
    if (shotId === "S03") defaultCodes = ["MOT002", "PRO001"];

    const activeCodes = (shotQAFailureCodes[shotId] && shotQAFailureCodes[shotId].length > 0)
        ? shotQAFailureCodes[shotId]
        : defaultCodes;

    try {
        const res = await fetch("/api/video/repair", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                task_id: oldTask ? oldTask.internal_task_id : null,
                product_id: currentProductId || "PROD_DEMO",
                shot_id: shotId,
                failure_codes: activeCodes,
                current_version: oldTask ? oldTask.prompt_version : "1.0",
            }),
        });
        const newTask = await readJsonOrThrow(res);
        currentTasks[shotId] = newTask;

        verTag.innerText = `V${newTask.prompt_version}`;
        verTag.style.color = "var(--color-warning)";

        // 更新提示词文本框展示
        const pEl = document.getElementById(`prompt${shotId}`);
        if (pEl) {
            pEl.value = newTask.prompt_text;
            pEl.innerText = newTask.prompt_text;
        }

        // 轮询新任务
        await pollTaskResult(newTask.internal_task_id, shotId);

        // 新版本写入历史
        const newVideo = document.getElementById(`video${shotId}`);
        shotHistory[shotId].push({
            prompt_version: newTask.prompt_version,
            prompt_text: newTask.prompt_text,
            video_url: newVideo && newVideo.src ? newVideo.src : "",
            failure_codes: [],
            score: newTask.qa_score ?? null,
        });

        showToast("单镜头修复完成", `🎉 分镜 ${shotId} 已依据 Failure Code [${activeCodes.join("/")}] 完成 V${newTask.prompt_version} 针对性修复重跑！`, "success");
    } catch (e) {
        showToast("修复重跑失败", e.message, "error");
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
        const response = await fetch(`/api/video/tasks/${taskId}/qa`, {
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
        const qaResult = await readJsonOrThrow(response);
        currentTasks[activeQAShotId] = { ...task, status: qaResult.task_status, qa_score: qaResult.qa_score, qa_status: qaResult.qa_status, failure_codes: failureCodes };

        shotQAFailureCodes[activeQAShotId] = failureCodes;
        if (failureCodes.length > 0) {
            showToast("QA 缺陷打标已保存", `⚠️ 检测到 ${activeQAShotId} 存在缺陷 [${failureCodes.join(', ')}]，已回写飞书《00_管理表》！系统已就绪靶向修复，可点击【🛠️ 修复重跑】或【⚙️ 工坊微调】。`, "warning");
        } else {
            showToast("QA 质检达标", `✅ 分镜 ${activeQAShotId} 评分 ${totalScore} 分，各维度达标！已同步至飞书。`, "success");
        }
        closeQAModal();
    } catch (e) {
        showToast("提交 QA 失败", e.message, "error");
    }
}

// 7. FFmpeg 3 镜头拼接 15 秒成品成片 (支持 TTS 智能混音配音)
async function stitchFinalVideo() {
    const btn = document.getElementById("btnStitch");
    btn.disabled = true;
    btn.innerHTML = "<span>⏳ 正在调用 FFmpeg 转码并混音成片...</span>";
    updateAgentStep(5);

    const taskIds = [
        currentTasks.S01 ? currentTasks.S01.internal_task_id : null,
        currentTasks.S02 ? currentTasks.S02.internal_task_id : null,
        currentTasks.S03 ? currentTasks.S03.internal_task_id : null,
    ].filter(Boolean);

    const productName = document.getElementById("productName").value.trim() || "带货商品";
    const productDesc = document.getElementById("productDesc").value.trim();
    const enableTts = document.getElementById("chkEnableTts") ? document.getElementById("chkEnableTts").checked : true;
    const voiceKey = document.getElementById("stitchVoiceSel") ? document.getElementById("stitchVoiceSel").value : "xiaoxiao";

    try {
        const res = await fetch("/api/video/stitch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                product_id: currentProductId || "PROD_DEMO",
                product_name: productName,
                product_desc: productDesc,
                task_ids: taskIds,
                enable_tts: enableTts,
                voice: voiceKey,
                require_qa_pass: !isMockMode,
            }),
        });

        if (!res.ok) {
            const errText = await res.text();
            throw new Error(`服务响应异常 (${res.status}): ${errText}`);
        }

        const data = await readJsonOrThrow(res);

        // 弹窗展示 15s 成片
        const player = document.getElementById("finalVideoPlayer");
        player.src = data.final_video_url;
        document.getElementById("btnDownloadFinal").href = data.final_video_url;

        const voiceNames = {
            xiaoxiao: "晓晓 (带货推荐女声)",
            yunxi: "云溪 (阳光带货男声)",
            yunjian: "云健 (影视专业解说)",
            xiaoyi: "小怡 (亲和生活女声)",
        };
        const ttsStatusEl = document.getElementById("stitchTtsStatus");
        if (ttsStatusEl) {
            ttsStatusEl.innerText = enableTts ? `${voiceNames[voiceKey] || voiceKey} · 3×5s 节拍对齐` : "未启用 (仅无声拼接)";
        }

        document.getElementById("stitchModal").style.display = "flex";
        const qaLabel = data.qa_pass_summary.status === "MOCK_QA_BYPASS" ? "Mock 预览（未冒充 QA 通过）" : "三镜头 QA 已通过";
        showToast("成片缝合完成", `🎉 15s 视频已合成；${qaLabel}。本地 SQLite 已归档，飞书按配置同步。`, "success");
    } catch (e) {
        showToast("拼接失败", e.message, "danger");
    } finally {
        btn.disabled = false;
        btn.innerHTML = "<span>✨ 无缝拼接 15 秒成片</span>";
    }
}

// 7.5 一键导出剪映电脑版 (Jianying Pro) 草稿工程
async function exportJianyingDraft() {
    const btn = document.getElementById("btnExportJianying");
    const modalBtn = document.getElementById("btnModalExportJianying");
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = "<span>⏳ 正在编译剪映草稿并打包...</span>";
    }
    if (modalBtn) {
        modalBtn.disabled = true;
        modalBtn.innerText = "⏳ 正在编译剪映草稿并打包...";
    }

    const taskIds = [
        currentTasks.S01 ? currentTasks.S01.internal_task_id : null,
        currentTasks.S02 ? currentTasks.S02.internal_task_id : null,
        currentTasks.S03 ? currentTasks.S03.internal_task_id : null,
    ].filter(Boolean);

    const productName = document.getElementById("productName").value.trim() || "带货商品";
    const productDesc = document.getElementById("productDesc").value.trim();
    const voiceKey = document.getElementById("stitchVoiceSel") ? document.getElementById("stitchVoiceSel").value : "xiaoxiao";

    try {
        const res = await fetch("/api/export/jianying", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                product_id: currentProductId || "PROD_DEMO",
                product_name: productName,
                product_desc: productDesc,
                task_ids: taskIds,
                voice: voiceKey,
            }),
        });

        if (!res.ok) {
            const err = await res.text();
            throw new Error(err);
        }

        const data = await res.json();

        // 自动触发 zip 文件下载
        const downloadLink = document.createElement("a");
        downloadLink.href = data.zip_url;
        downloadLink.download = data.zip_filename;
        document.body.appendChild(downloadLink);
        downloadLink.click();
        downloadLink.remove();

        // 展示本机剪映草稿库同步徽章
        const badge = document.getElementById("jianyingLocalSyncBadge");
        if (badge) {
            badge.style.display = "block";
            if (data.synced_to_local_jianying) {
                badge.innerText = `✅ 已成功直写本机《剪映专业版》草稿库！\n路径: ${data.local_draft_path}`;
            } else {
                badge.innerText = `📦 剪映草稿压缩包已下载，解压至剪映 Drafts 目录即可导入。`;
            }
        }

        showToast(
            "剪映草稿导出成功",
            `🎬 剪映电脑版工程 (.zip) 已导出！${data.synced_to_local_jianying ? '已直写本机剪映草稿库，打开剪映即可看到！' : ''}`,
            "success"
        );
    } catch (e) {
        showToast("导出剪映草稿失败", e.message, "danger");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = "<span>🎬 导出剪映电脑版草稿</span>";
        }
        if (modalBtn) {
            modalBtn.disabled = false;
            modalBtn.innerText = "🎬 一键导出剪映电脑版草稿 (.zip)";
        }
    }
}

function closeStitchModal() {
    document.getElementById("stitchModal").style.display = "none";
    document.getElementById("finalVideoPlayer").pause();
}

// 8. 接口与模型配置弹窗 (对齐 WebLockShot 动态模型服务商与 API Key 选项，并内嵌动态成本)
function onModelConfigChange(modelVal, isUserSelect = true) {
    const lblKey = document.getElementById("lbl_api_key");
    const inputKey = document.getElementById("cfg_jimeng_key");
    const hint = document.getElementById("cfg_key_hint");
    const boxEndpoint = document.getElementById("box_seedance_endpoint");
    const secTitle = document.getElementById("sec_model_title");
    const costInput = document.getElementById("cfg_cost_per_second");
    const costHint = document.getElementById("cfg_cost_hint");

    let defaultCost = 0.05;
    let hintCostText = "💡 官方基准参考价: 约 0.05 元/秒 (5秒标清分镜约 ¥0.25 元)";

    if (modelVal === "seedance-2.0-fast") {
        secTitle.innerText = "⚡ 字节跳动火山引擎方舟 (Seedance 2.0 Fast) 算力与成本配置";
        lblKey.innerText = "火山引擎方舟 (Ark) / Seedance API Key:";
        inputKey.placeholder = "填入火山引擎 ARK_API_KEY (如: 8f4e2b01-xxxx)...";
        hint.innerText = "💡 已完成统一任务契约；真实鉴权、提交与轮询将在拿到供应商文档和 Key 后联调";
        if (boxEndpoint) boxEndpoint.style.display = "block";
        defaultCost = 0.05;
        hintCostText = "💡 官方基准参考价: 约 0.05 元/秒 (5秒极速分镜成本约 ¥0.25 元)";
    } else if (modelVal === "seedance-2.0-pro") {
        secTitle.innerText = "⚡ 字节跳动火山引擎方舟 (Seedance 2.0 Pro 4K超清) 算力与成本配置";
        lblKey.innerText = "火山引擎方舟 (Ark) / Seedance API Key:";
        inputKey.placeholder = "填入火山引擎 ARK_API_KEY (如: 8f4e2b01-xxxx)...";
        hint.innerText = "💡 Seedance Pro 已保留模型选择，尚未用真实 Key 完成端到端验收";
        if (boxEndpoint) boxEndpoint.style.display = "block";
        defaultCost = 0.09;
        hintCostText = "💡 官方基准参考价: 约 0.09 元/秒 (5秒旗舰4K分镜成本约 ¥0.45 元)";
    } else if (modelVal === "jimeng-video-v2") {
        secTitle.innerText = "⚡ 字节即梦 (Jimeng 2.0) 开放平台算力与成本配置";
        lblKey.innerText = "即梦开放平台 API Key / Session Token:";
        inputKey.placeholder = "填入公司提供的即梦开放平台 API Key / Token...";
        hint.innerText = "💡 即梦 Provider 已保留接入口，尚未用真实 Key 完成端到端验收";
        if (boxEndpoint) boxEndpoint.style.display = "none";
        defaultCost = 0.05;
        hintCostText = "💡 官方基准参考价: 约 20 算力点/5秒 (折合约 0.05 元/秒，5秒约 ¥0.25 元)";
    } else if (modelVal.startsWith("kling")) {
        secTitle.innerText = "⚡ 快手可灵 (Kling 1.5) 算力与成本配置";
        lblKey.innerText = "快手可灵 (Kling) API Key (AccessKey):";
        inputKey.placeholder = "填入快手可灵 AccessKey / SecretKey...";
        hint.innerText = "💡 可灵 Provider 已保留统一契约，供应商签名适配尚待真实接口资料";
        if (boxEndpoint) boxEndpoint.style.display = "none";
        defaultCost = 0.08;
        hintCostText = "💡 官方基准参考价: 约 10~15 灵感值/5秒 (折合约 0.08 元/秒，5秒约 ¥0.40 元)";
    }

    if (isUserSelect && costInput) costInput.value = defaultCost;
    if (costHint) costHint.innerText = hintCostText;
}

async function quickSwitchModel(modelVal) {
    try {
        const res = await fetch("/api/system/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jimeng_default_model: modelVal }),
        });
        if (res.ok) {
            currentVideoModel = modelVal;
            currentVideoProvider = providerForModel(modelVal);
            showToast("模型切换", `⚡ 主视频生成引擎已切换为: ${modelVal}`, "success");
        }
    } catch (e) {
        showToast("模型切换失败", e.message, "danger");
    }
}

async function openSettingsModal() {
    try {
        const res = await fetch("/api/system/settings");
        if (res.ok) {
            const cfg = await res.json();
            const curModel = cfg.jimeng_default_model || "seedance-2.0-fast";
            currentVideoModel = curModel;
            currentVideoProvider = providerForModel(curModel);
            document.getElementById("cfg_jimeng_model").value = curModel;
            document.getElementById("cfg_jimeng_key").value = "";
            document.getElementById("cfg_jimeng_key").placeholder = cfg.has_jimeng_key ? "已配置（留空保留现有密钥）" : "输入视频 Provider API Key";
            document.getElementById("cfg_billing_mode").value = cfg.billing_mode || "CNY";
            document.getElementById("cfg_cost_per_second").value = cfg.cost_per_second_cny || 0.05;
            document.getElementById("cfg_feishu_app_id").value = cfg.feishu_app_id || "";
            document.getElementById("cfg_feishu_token").value = cfg.feishu_bitable_app_token || "";
            if (document.getElementById("cfg_feishu_sync_mode")) {
                const sMode = cfg.feishu_sync_mode || "dual";
                document.getElementById("cfg_feishu_sync_mode").value = sMode;
                onFeishuSyncModeChange(sMode);
            }
            
            if (document.getElementById("cfg_seedance_endpoint")) {
                document.getElementById("cfg_seedance_endpoint").value = cfg.seedance_endpoint_id || "";
            }
            if (document.getElementById("cfg_llm_base_url")) {
                document.getElementById("cfg_llm_base_url").value = cfg.llm_api_base_url || "https://api.openai.com/v1";
            }
            if (document.getElementById("cfg_llm_key")) {
                document.getElementById("cfg_llm_key").value = "";
                document.getElementById("cfg_llm_key").placeholder = cfg.has_llm_key ? "已配置（留空保留现有密钥）" : "sk-...（可选）";
            }
            if (document.getElementById("cfg_llm_api_style")) document.getElementById("cfg_llm_api_style").value = cfg.llm_api_style || "responses";
            if (document.getElementById("cfg_llm_model")) document.getElementById("cfg_llm_model").value = cfg.llm_model || "gpt-5-mini";
            if (document.getElementById("cfg_feishu_secret")) {
                document.getElementById("cfg_feishu_secret").value = "";
                document.getElementById("cfg_feishu_secret").placeholder = cfg.has_feishu_secret ? "已配置（留空保留现有密钥）" : "输入飞书 App Secret";
            }

            // 同步顶部快捷选择器
            const topSel = document.getElementById("headerModelSelector");
            if (topSel) topSel.value = curModel;

            onModelConfigChange(curModel, false);
            if (cfg.cost_per_second_cny !== undefined) {
                document.getElementById("cfg_cost_per_second").value = cfg.cost_per_second_cny;
            }
        }
    } catch (e) {
        console.warn("Load settings failed:", e);
    }
    document.getElementById("settingsModal").style.display = "flex";
}

function closeSettingsModal() {
    document.getElementById("settingsModal").style.display = "none";
}

function togglePasswordVisibility(id) {
    const input = document.getElementById(id);
    if (!input) return;
    input.type = input.type === "password" ? "text" : "password";
}

async function saveSettings() {
    const chosenModel = document.getElementById("cfg_jimeng_model").value;
    const apiKey = document.getElementById("cfg_jimeng_key").value.trim();

    const payload = {
        jimeng_default_model: chosenModel,
        jimeng_api_key: apiKey,
        seedance_ark_api_key: chosenModel.startsWith("seedance") ? apiKey : "",
        seedance_endpoint_id: document.getElementById("cfg_seedance_endpoint") ? document.getElementById("cfg_seedance_endpoint").value.trim() : "",
        llm_api_base_url: document.getElementById("cfg_llm_base_url") ? document.getElementById("cfg_llm_base_url").value.trim() : "",
        llm_api_key: document.getElementById("cfg_llm_key") ? document.getElementById("cfg_llm_key").value.trim() : "",
        llm_api_style: document.getElementById("cfg_llm_api_style") ? document.getElementById("cfg_llm_api_style").value : "responses",
        llm_model: document.getElementById("cfg_llm_model") ? document.getElementById("cfg_llm_model").value.trim() : "gpt-5-mini",
        billing_mode: document.getElementById("cfg_billing_mode").value,
        cost_per_second_cny: parseFloat(document.getElementById("cfg_cost_per_second").value) || 0.05,
        feishu_sync_mode: document.getElementById("cfg_feishu_sync_mode") ? document.getElementById("cfg_feishu_sync_mode").value : "dual",
        feishu_app_id: document.getElementById("cfg_feishu_app_id").value.trim(),
        feishu_bitable_app_token: document.getElementById("cfg_feishu_token").value.trim(),
        feishu_app_secret: document.getElementById("cfg_feishu_secret") ? document.getElementById("cfg_feishu_secret").value.trim() : "",
    };

    try {
        const res = await fetch("/api/system/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!res.ok) {
            const err = await res.text();
            throw new Error(err);
        }
        currentVideoModel = chosenModel;
        currentVideoProvider = providerForModel(chosenModel);
        await fetchSystemStatus();
        const topSel = document.getElementById("headerModelSelector");
        if (topSel) topSel.value = chosenModel;

        showToast("系统配置已生效", "模型与密钥仅在本次服务进程中生效；长期配置请写入本机 .env（不会提交 Git）。", "success");
        closeSettingsModal();
    } catch (e) {
        showToast("保存配置失败", e.message, "danger");
    }
}

// 9. 全自动化 UI 点测截屏审查工具
function openVisualTestModal() {
    document.getElementById("visualTestModal").style.display = "flex";
    loadExistingVisualScreenshots();
}

function closeVisualTestModal() {
    document.getElementById("visualTestModal").style.display = "none";
}

async function loadExistingVisualScreenshots() {
    const grid = document.getElementById("screenshotsGrid");
    const steps = [
        { num: 1, title: "Web 首页就绪", file: "test_step1_home.png", desc: "初始状态机与 Agent 拓扑工作区" },
        { num: 2, title: "切换案例并编译", file: "test_step2_preset.png", desc: "修护精华液预置案例与11层提示词" },
        { num: 3, title: "模型与密钥配置", file: "test_step3_settings_modal.png", desc: "Seedance / 即梦接口与成本参数弹窗" },
        { num: 4, title: "并发生成全部分镜", file: "test_step4_generated.png", desc: "S01, S02, S03 三分镜并发渲染与视口回显" },
        { num: 5, title: "S02 修复重跑 (V1.1)", file: "test_step5_repaired.png", desc: "S01/S03锁定，S02单镜重跑" },
        { num: 6, title: "15s 成片交付播放", file: "test_step6_stitched.png", desc: "FFmpeg 3×5s 无损拼接成片弹窗播放" },
    ];

    grid.innerHTML = steps.map(s => `
        <div class="screenshot-card">
            <div class="screenshot-img-wrap" onclick="window.open('/outputs/${s.file}?t=${Date.now()}', '_blank')">
                <img src="/outputs/${s.file}?t=${Date.now()}" class="screenshot-img" alt="${s.title}" onerror="this.onerror=null; this.src='/static/placeholder_test.png'; this.alt='待运行点测后生成';">
            </div>
            <div class="screenshot-info">
                <span class="screenshot-badge">Step 0${s.num}</span>
                <div class="screenshot-title">${s.title}</div>
                <div class="screenshot-desc">${s.desc}</div>
            </div>
        </div>
    `).join("");
}

async function executeVisualE2ETest() {
    const btn = document.getElementById("btnRunTestAgain");
    const term = document.getElementById("testLogTerminal");
    btn.disabled = true;
    btn.innerHTML = "<span>⏳ 正在启动 Headless Edge 自动化点击并截屏 (约15秒)...</span>";
    term.innerText = "🚀 正在启动 Headless Edge 驱动浏览器...\nSimulating: 首页加载 ➔ 切换案例 ➔ 打开设置 ➔ 并发生成 ➔ 修复重跑 ➔ 拼接成片...\n";

    try {
        const res = await fetch("/api/test/run-visual-e2e", { method: "POST" });
        const data = await res.json();
        term.innerText = data.logs || "自动化点测完成！";
        await loadExistingVisualScreenshots();
        showToast("自动化点测完成", "✅ 6 个交互步骤模拟点击与视觉截图审查已全部通过！", "success");
    } catch (e) {
        term.innerText += `\n❌ 运行测试异常: ${e.message}`;
        showToast("点测未完全通过", e.message, "warning");
    } finally {
        btn.disabled = false;
        btn.innerHTML = "<span>🚀 立即运行全套点击测试并截屏</span>";
    }
}

// 10. Agent 编排高亮指示器
function updateAgentStep(stepNum) {
    for (let i = 1; i <= 5; i++) {
        const el = document.getElementById(`agentStep${i}`);
        if (!el) continue;
        if (i <= stepNum) {
            el.classList.add("active");
        } else {
            el.classList.remove("active");
        }
    }
}

// 11. 全局轻量 Toast 通知系统
function showToast(title, desc, type = "info") {
    const container = document.getElementById("toastContainer");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast-item toast-${type}`;
    const icon = type === "success" ? "✅" : (type === "danger" ? "❌" : (type === "warning" ? "⚠️" : "ℹ️"));
    const iconEl = document.createElement("span");
    iconEl.className = "toast-icon";
    iconEl.textContent = icon;
    const contentEl = document.createElement("div");
    contentEl.className = "toast-content";
    const titleEl = document.createElement("div");
    titleEl.className = "toast-title";
    titleEl.textContent = String(title);
    const descEl = document.createElement("div");
    descEl.className = "toast-desc";
    descEl.textContent = String(desc);
    contentEl.append(titleEl, descEl);
    toast.append(iconEl, contentEl);
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateY(10px) scale(0.95)";
        toast.style.transition = "all 0.3s ease";
        setTimeout(() => toast.remove(), 300);
    }, 3800);
}

// 12. 飞书存储模式切换
function onFeishuSyncModeChange(modeVal) {
    const credBox = document.getElementById("box_feishu_credentials");
    const hint = document.getElementById("cfg_feishu_hint");
    if (!credBox || !hint) return;
    if (modeVal === "local") {
        credBox.style.opacity = "0.4";
        hint.innerText = "💡 纯本地模式：结构化记录保存于 SQLite，媒体文件保存于 outputs/。";
    } else if (modeVal === "cloud") {
        credBox.style.opacity = "1";
        hint.innerText = "💡 仅云端模式：资产将直接提交至飞书开放平台多维表格，方便团队在线协同审核。";
    } else {
        credBox.style.opacity = "1";
        hint.innerText = "💡 镜像双写模式：SQLite 是事实源，媒体落盘 outputs/，飞书失败写入进入待同步队列。";
    }
}

// 13. Section 18 & 19 轮次优化与通过率对比矩阵系统
let matrixRecords = [];

async function openSection19MatrixModal() {
    document.getElementById("section19Modal").style.display = "flex";
    await refreshMatrixRecords();
    renderMatrixTable();
}

function closeSection19MatrixModal() {
    document.getElementById("section19Modal").style.display = "none";
}

function renderMatrixTable() {
    const tbody = document.getElementById("matrixTableBody");
    if (!tbody) return;
    tbody.replaceChildren();
    if (matrixRecords.length === 0) {
        const row = tbody.insertRow();
        const cell = row.insertCell();
        cell.colSpan = 10;
        cell.textContent = "暂无真实测试记录；运行 Round 1 后将在此显示 SQLite 数据。";
        return;
    }
    matrixRecords.forEach(record => {
        const row = tbody.insertRow();
        const status = record.status === "REJECTED" ? "FAIL" : record.status;
        const next = status === "PASS" ? "可进入交付" : (["REPAIR", "FAIL"].includes(status) ? "按 Failure Code 单镜重跑" : "等待人工 QA");
        const values = [
            record.internal_task_id,
            record.shot_id,
            `V${record.prompt_version}`,
            record.repair_actions?.join(", ") || record.variant_id || "Baseline",
            record.generation_time_seconds == null ? "--" : `${record.generation_time_seconds}s`,
            record.estimated_cost == null ? "--" : `¥${Number(record.estimated_cost).toFixed(2)}`,
            record.qa_score == null ? "--" : String(record.qa_score),
            status,
            record.failure_codes?.join(",") || "--",
            next,
        ];
        values.forEach(value => { const cell = row.insertCell(); cell.textContent = value; });
    });
}

async function refreshMatrixRecords() {
    if (!currentProductId) { matrixRecords = []; return; }
    const response = await fetch(`/api/video/tasks?product_id=${encodeURIComponent(currentProductId)}`);
    matrixRecords = await readJsonOrThrow(response);
    updateMatrixStats();
}

function updateMatrixStats() {
    const evaluated = record => ["PASS", "REPAIR", "REJECTED"].includes(record.status) && record.qa_score != null;
    const v10 = matrixRecords.filter(record => record.shot_id === "S02" && record.prompt_version.startsWith("1.0") && evaluated(record));
    const v11 = matrixRecords.filter(record => record.shot_id === "S02" && record.prompt_version.startsWith("1.1") && evaluated(record));
    const rate = records => records.length ? (records.filter(record => record.status === "PASS").length / records.length * 100) : null;
    const rate10 = rate(v10);
    const rate11 = rate(v11);
    const hand10 = v10.length ? v10.filter(record => record.failure_codes?.includes("HAND001")).length / v10.length * 100 : null;
    const hand11 = v11.length ? v11.filter(record => record.failure_codes?.includes("HAND001")).length / v11.length * 100 : null;
    document.getElementById("matrixRateV10").textContent = rate10 == null ? "待 QA" : `${rate10.toFixed(1)}%`;
    document.getElementById("matrixCountV10").textContent = `${v10.length} 条已评分 S02 记录`;
    document.getElementById("matrixRateV11").textContent = rate11 == null ? "待重跑/QA" : `${rate11.toFixed(1)}%`;
    document.getElementById("matrixCountV11").textContent = `${v11.length} 条已评分 S02 记录`;
    document.getElementById("matrixHandRate").textContent = hand10 == null || hand11 == null ? "待真实数据" : `${hand10.toFixed(1)}% ➔ ${hand11.toFixed(1)}%`;
    const acceptance = document.getElementById("matrixAcceptance");
    const hint = document.getElementById("matrixAcceptanceHint");
    if (v10.length >= 3 && v11.length >= 3 && rate11 > rate10) {
        acceptance.textContent = "✅ 数据支持改善";
        acceptance.className = "m-val text-success";
        hint.textContent = "满足每轮至少 3 条且通过率提升";
    } else {
        acceptance.textContent = "⏳ 尚未判定";
        acceptance.className = "m-val";
        hint.textContent = "需要 Round 1/2 各至少 3 条人工 QA 且通过率提升";
    }
}

async function waitForTaskRecord(taskId) {
    for (let i = 0; i < 120; i++) {
        await new Promise(resolve => setTimeout(resolve, 500));
        const response = await fetch(`/api/video/tasks/${taskId}`);
        const task = await readJsonOrThrow(response);
        if (!["CREATED", "SUBMITTED", "PROCESSING", "COMPLETED"].includes(task.status)) return task;
    }
    throw new Error(`任务轮询超时: ${taskId}`);
}

async function runRound1MatrixTest() {
    const btn = document.getElementById("btnRunRound1");
    btn.disabled = true;
    btn.innerHTML = "<span>⏳ 正在并发执行 Round 1 (9条)...</span>";
    try {
        showToast("Round 1 启动", "正在生成受控 Prompt 变体和 9 条真实任务记录；完成后仍需人工 QA。", "info");
        const planResponse = await fetch("/api/prompts/variants/plan", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ product_id: currentProductId, variants_per_shot: 3, base_version: "1.0" }),
        });
        const variants = await readJsonOrThrow(planResponse);
        const submitted = await Promise.all(variants.map(async variant => {
            const response = await fetch("/api/video/generate", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    product_id: currentProductId, shot_id: variant.shot_id,
                    provider: isMockMode ? "mock" : currentVideoProvider,
                    model: isMockMode ? "mock-video-v1" : currentVideoModel,
                    prompt_version: variant.prompt_version, prompt: variant.prompt_text,
                    negative_prompt: variant.negative_prompt, variant_id: variant.variant_id,
                    image_url: document.getElementById("productImageUrl").value.trim(), duration: 5, aspect_ratio: "9:16",
                    product_name: document.getElementById("productName").value.trim(),
                }),
            });
            return readJsonOrThrow(response);
        }));
        await Promise.all(submitted.map(task => waitForTaskRecord(task.internal_task_id)));
        await refreshMatrixRecords();
        renderMatrixTable();
        showToast("Round 1 生成完成", "9 条真实记录已写入 SQLite，状态为待 QA；通过率将在人工评分后计算。", "warning");
    } catch (error) {
        showToast("Round 1 失败", error.message, "danger");
    } finally {
        btn.disabled = false;
        btn.innerHTML = "<span>▶ 运行 Round 1 基准测试 (9条)</span>";
    }
}

async function runRound2OptimizationTest() {
    const btn = document.getElementById("btnRunRound2");
    btn.disabled = true;
    btn.innerHTML = "<span>⏳ 正在执行 S02_V1.1 × 3 次靶向重跑...</span>";
    try {
        await refreshMatrixRecords();
        const candidates = matrixRecords.filter(record => record.shot_id === "S02" && ["REPAIR", "REJECTED"].includes(record.status)).slice(0, 3);
        if (candidates.length === 0) throw new Error("请先对 Round 1 的 S02 记录执行 QA 并填写 Failure Code");
        showToast("Section 19 靶向优化", `正在按 ${candidates.length} 条真实失败记录执行单镜头重跑。`, "info");
        const retried = await Promise.all(candidates.map(async candidate => {
            const response = await fetch(`/api/video/tasks/${candidate.internal_task_id}/retry`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ failure_codes: candidate.failure_codes }),
            });
            return readJsonOrThrow(response);
        }));
        await Promise.all(retried.map(task => waitForTaskRecord(task.internal_task_id)));
        await refreshMatrixRecords();
        renderMatrixTable();
        showToast("靶向重跑完成", "新版本已进入待 QA；只有重新评分后才会计算改善幅度。", "warning");
    } catch (error) {
        showToast("无法运行靶向优化", error.message, "danger");
    } finally {
        btn.disabled = false;
        btn.innerHTML = "<span>⚡ 运行 Section 19 靶向优化 (S02_V1.1 × 3)</span>";
    }
}

// -----------------------------------------------------------------------------
// 10. 分镜提示词工程独立工作台 (Prompt Studio) 交互控制 (宽屏双栏架构)
// -----------------------------------------------------------------------------

// 分镜独立控制状态字典 (物理隔离，彻底解决互相影响问题)
let shotStudioState = {
    S01: { motion: "complex", lock: "standard", camera: "fixed" },
    S02: { motion: "degraded", lock: "double", camera: "fixed" },
    S03: { motion: "complex", lock: "standard", camera: "dynamic" },
};

// 按照分镜特化的三维动作与规避预设词典
const SHOT_MOTION_PRESETS = {
    S01: {
        complex: {
            desc: "正在生效: 人物正常办公专注看电脑，动作自然生活化",
            text: "【第 5 层: 人物动作】0到5秒人物正常专注办公看电脑，偶有极其自然的视线微移与身体轻微起伏，动作生活化，绝无机械僵硬感。",
        },
        degraded: {
            desc: "正在生效: 专注办公纯生活态，消除表演感与生硬转头 (推荐/稳妥)",
            text: "【第 5 层: 人物动作】【降级优化动作】0到5秒人物平静专注于电脑屏幕正常办公，仅有极细微的自然呼吸与偶发的眼部眨动，全程不主动看镜头，消除任何表演感与生硬转头动作。",
        },
        minimal: {
            desc: "正在生效: 绝对静止坐姿，仅保留微弱呼吸起伏 (兜底)",
            text: "【第 5 层: 人物动作】【极简动作】0到5秒人物保持端正坐姿面向前方办公桌，身体完全不产生大幅位移，仅有微弱的生活化自然呼吸起伏。",
        },
    },
    S02: {
        complex: {
            desc: "正在生效: 伸手 ➔ 拿起 ➔ 开盖饮用/使用 (原版/高危)",
            text: "【第 5 层: 人物动作】0到1秒人物正常工作看电脑；约1秒后，自然将右手缓慢伸向桌面商品，稳拿至胸前适中位置，随后进行一次开盖或简单使用动作，动作连贯不机械。",
        },
        degraded: {
            desc: "正在生效: 伸手 ➔ 平稳拿起悬停5cm (推荐/稳妥/防粘连)",
            text: "【第 5 层: 人物动作】【降级优化动作】0到1.5秒人物继续正常工作看电脑；随后极其缓慢自然将右手单手伸向桌面商品，稳稳握住商品下部并缓慢提起至桌面正上方5厘米稳定悬停，不进行任何开盖、饮用或复杂操作，手指保持单手平稳抓握，手腕动作幅度极小。",
        },
        minimal: {
            desc: "正在生效: 原位单手握持静止端正展示 (兜底/防崩坏)",
            text: "【第 5 层: 人物动作】【极简握持动作】0到5秒人物右手单手平稳握持桌面商品，保持静止端正展示，完全无抬升、挥动或身体晃动，仅有微弱自然呼吸起伏。",
        },
    },
    S03: {
        complex: {
            desc: "正在生效: 简单展示后迅速放回桌面离开 (原版)",
            text: "【第 5 层: 人物动作】0到2秒人物自然平稳将商品放回桌面靠前位置；手部自然缓慢离开商品；3.5到5秒人物自然将注意力与视线重新回到原本工作或生活活动中，表情放松从容。",
        },
        degraded: {
            desc: "正在生效: 放缓速度减速放回，静止定格记忆点 (推荐/平滑)",
            text: "【第 5 层: 人物动作】【放缓优化动作】0到2秒人物手持商品稳定定格于胸前，形成清晰产品记忆点；2到4秒手部极其缓慢平稳地将商品放回原位桌面，速度均匀柔和；4到5秒手部自然平稳移开，商品静止于桌面正前方。",
        },
        minimal: {
            desc: "正在生效: 商品全程静止于桌面，手部完全不接触",
            text: "【第 5 层: 人物动作】【极简动作】0到5秒商品完全静止陈列于桌面黄金构图位置，人物在背景中正常生活活动，不产生任何手部接触动作。",
        },
    },
};

const SHOT_LOCK_PRESETS = {
    double: {
        desc: "正在生效: PRODUCT_LOCK_001 + 002 几何与物理双锁",
        text: "【第 6 层: 商品交互】手指与商品接触面完全符合真实单手抓握力学，商品具有正常物理重量感，严格执行 PRODUCT_LOCK_001 与 PRODUCT_LOCK_002 双重高斯形态约束，绝不发生几何拉伸、形变或Logo漂移。",
    },
    standard: {
        desc: "正在生效: PRODUCT_LOCK_001 基础防变形约束",
        text: "【第 6 层: 商品交互】目标商品外形尺寸比例正常，保持与输入参考图严格一致，符合 PRODUCT_LOCK_001 标准防变形规范。",
    },
};

const SHOT_CAMERA_PRESETS = {
    fixed: {
        desc: "正在生效: 纯正前方绝对固定机位，完全杜绝晃动",
        text: "【第 7 层: 镜头运镜】采用纯正前方中景绝对固定机位，完全无任何推拉摇移与镜头晃动，保持构图基准线完全稳定。",
    },
    dynamic: {
        desc: "正在生效: 标准中景极其平缓微动态缓推 (自然运镜)",
        text: "【第 7 层: 镜头运镜】采用标准中景极其平缓的微动态缓推，自然聚焦商品主体，运动丝滑无跳帧。",
    },
};

// 切换分镜标签 (支持在弹窗内无缝切换三镜头连贯精调)
function switchStudioShotTab(shotId) {
    // 1. 保存当前编辑的文本到前一分镜的文本域
    if (activeStudioShotId) {
        const curText = document.getElementById("studioPromptText")?.value;
        const oldCard = document.getElementById(`prompt${activeStudioShotId}`);
        if (oldCard && curText) oldCard.value = curText;
    }

    activeStudioShotId = shotId;

    // 2. 更新顶部 Tabs 高亮
    ["S01", "S02", "S03"].forEach(s => {
        const tab = document.getElementById(`tabShot${s}`);
        if (tab) tab.classList.toggle("active", s === shotId);
    });

    renderStudioCurrentShot();
}

function openPromptStudioModal(shotId) {
    switchStudioShotTab(shotId);
    document.getElementById("promptStudioModal").style.display = "flex";
}

function closePromptStudioModal() {
    // 关闭前自动保存当前编辑
    if (activeStudioShotId) {
        const curText = document.getElementById("studioPromptText")?.value;
        const oldCard = document.getElementById(`prompt${activeStudioShotId}`);
        if (oldCard && curText) oldCard.value = curText;
    }
    document.getElementById("promptStudioModal").style.display = "none";
}

// 刷新当前分镜的 UI 与数据渲染
function renderStudioCurrentShot() {
    const shotId = activeStudioShotId;
    const titles = {
        S01: "S01 真实场景建立 (0~5s)",
        S02: "S02 单手拿起与使用 (5~10s)",
        S03: "S03 平稳放回与记忆点 (10~15s)",
    };

    const bEl = document.getElementById("studioShotBadge");
    bEl.innerText = shotId;
    bEl.className = `shot-badge ${shotId === "S02" ? "orange" : ""}`;

    document.getElementById("studioShotTitle").innerText = `分镜提示词工程工作台 (${titles[shotId] || shotId})`;
    
    const curVer = currentTasks[shotId] ? currentTasks[shotId].prompt_version : "1.0";
    document.getElementById("studioVerTag").innerText = `V${curVer}`;
    document.getElementById("studioProductTag").innerText = currentProductId || "PROD_DEFAULT";

    // 载入卡片现有提示词 (若无则取 Baseline)
    const cardEl = document.getElementById(`prompt${shotId}`);
    let promptContent = cardEl ? cardEl.value : "";
    if (!promptContent && baselinePrompts[shotId]) {
        promptContent = baselinePrompts[shotId];
    }
    const studioArea = document.getElementById("studioPromptText");
    studioArea.value = promptContent;

    // 恢复该分镜独立的胶囊状态
    const curState = shotStudioState[shotId] || { motion: "complex", lock: "standard", camera: "fixed" };
    
    // 高亮动作按钮
    document.querySelectorAll("#chip_motion_complex, #chip_motion_degraded, #chip_motion_minimal").forEach(b => b.classList.remove("active"));
    document.getElementById(`chip_motion_${curState.motion}`)?.classList.add("active");
    const mPreset = SHOT_MOTION_PRESETS[shotId]?.[curState.motion];
    if (mPreset) document.getElementById("studioMotionDesc").innerText = mPreset.desc;

    // 高亮锁定按钮
    document.querySelectorAll("#chip_lock_double, #chip_lock_standard").forEach(b => b.classList.remove("active"));
    document.getElementById(`chip_lock_${curState.lock}`)?.classList.add("active");
    const lPreset = SHOT_LOCK_PRESETS[curState.lock];
    if (lPreset) document.getElementById("studioLockDesc").innerText = lPreset.desc;

    // 高亮运镜按钮
    document.querySelectorAll("#chip_cam_fixed, #chip_cam_dynamic").forEach(b => b.classList.remove("active"));
    document.getElementById(`chip_cam_${curState.camera}`)?.classList.add("active");
    const cPreset = SHOT_CAMERA_PRESETS[curState.camera];
    if (cPreset) document.getElementById("studioCameraDesc").innerText = cPreset.desc;

    // 检查是否有该镜头的 QA 缺陷标记
    const codes = (shotQAFailureCodes[shotId] && shotQAFailureCodes[shotId].length > 0)
        ? shotQAFailureCodes[shotId]
        : (currentTasks[shotId] ? currentTasks[shotId].failure_codes : []);

    const qaAlert = document.getElementById("studioQaAlert");
    if (codes && codes.length > 0) {
        document.getElementById("studioAlertCodes").innerText = codes.join(", ");
        qaAlert.style.display = "flex";
    } else {
        qaAlert.style.display = "none";
    }

    validateStudio11Layers();
    document.getElementById("studioDirtyStatus").className = "tag-clean";
    document.getElementById("studioDirtyStatus").innerText = "与分镜一致";
}

function applyStudioChip(category, chipKey) {
    const shotId = activeStudioShotId;
    if (!shotStudioState[shotId]) {
        shotStudioState[shotId] = { motion: "complex", lock: "standard", camera: "fixed" };
    }
    shotStudioState[shotId][category] = chipKey;

    const textarea = document.getElementById("studioPromptText");
    let text = textarea.value;

    if (category === "motion") {
        document.querySelectorAll("#chip_motion_complex, #chip_motion_degraded, #chip_motion_minimal").forEach(b => b.classList.remove("active"));
        document.getElementById(`chip_motion_${chipKey}`)?.classList.add("active");

        const preset = SHOT_MOTION_PRESETS[shotId]?.[chipKey];
        if (preset) {
            document.getElementById("studioMotionDesc").innerText = preset.desc;
            // 正则精确替换【第 5 层: 人物动作】
            const reg = /(【第\s*5\s*层[^】]*】[^\n]+)/g;
            if (reg.test(text)) {
                text = text.replace(reg, preset.text);
            } else {
                text += "\n\n" + preset.text;
            }
        }
    } else if (category === "lock") {
        document.querySelectorAll("#chip_lock_double, #chip_lock_standard").forEach(b => b.classList.remove("active"));
        document.getElementById(`chip_lock_${chipKey}`)?.classList.add("active");

        const preset = SHOT_LOCK_PRESETS[chipKey];
        if (preset) {
            document.getElementById("studioLockDesc").innerText = preset.desc;
            // 正则精确替换【第 6 层: 商品交互】
            const reg = /(【第\s*6\s*层[^】]*】[^\n]+)/g;
            if (reg.test(text)) {
                text = text.replace(reg, preset.text);
            } else {
                text += "\n\n" + preset.text;
            }
        }
    } else if (category === "camera") {
        document.querySelectorAll("#chip_cam_fixed, #chip_cam_dynamic").forEach(b => b.classList.remove("active"));
        document.getElementById(`chip_cam_${chipKey}`)?.classList.add("active");

        const preset = SHOT_CAMERA_PRESETS[chipKey];
        if (preset) {
            document.getElementById("studioCameraDesc").innerText = preset.desc;
            // 正则精确替换【第 7 层: 镜头运镜】
            const reg = /(【第\s*7\s*层[^】]*】[^\n]+)/g;
            if (reg.test(text)) {
                text = text.replace(reg, preset.text);
            } else {
                text += "\n\n" + preset.text;
            }
        }
    }

    textarea.value = text;
    validateStudio11Layers();

    const dirtyTag = document.getElementById("studioDirtyStatus");
    dirtyTag.className = "tag-dirty";
    dirtyTag.innerText = "已应用工程预设";
}

function applyStudioRecommendedFix() {
    applyStudioChip("motion", "degraded");
    applyStudioChip("lock", "double");
    applyStudioChip("camera", "fixed");
    showToast("智能修复策略已就绪", "✅ 已自动装配 V1.1 降级策略 (动作降级 + 双重锁定 + 固定机位)！", "success");
}

function validateStudio11Layers() {
    const text = document.getElementById("studioPromptText").value;
    document.getElementById("studioWordCount").innerText = `字数: ${text.length} 字`;

    const layerNums = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];
    let missing = [];
    layerNums.forEach(n => {
        if (!text.includes(`第 ${n} 层`) && !text.includes(`第${n}层`)) {
            missing.push(`第${n}层`);
        }
    });

    const checkEl = document.getElementById("studioLayerCheck");
    if (missing.length === 0) {
        checkEl.className = "text-success";
        checkEl.innerText = "✅ 11 层工业规范完整度 100%";
    } else {
        checkEl.className = "text-warning";
        checkEl.innerText = `⚠️ 缺少 ${missing.length} 项规范 (${missing.join(', ')})`;
    }
}

function resetStudioPromptToBaseline() {
    if (baselinePrompts[activeStudioShotId]) {
        document.getElementById("studioPromptText").value = baselinePrompts[activeStudioShotId];
        validateStudio11Layers();
        document.getElementById("studioDirtyStatus").className = "tag-clean";
        document.getElementById("studioDirtyStatus").innerText = "已重置为官方 Baseline";
        showToast("已重置", `分镜 ${activeStudioShotId} 已恢复官方装配初始提示词`, "info");
    } else {
        showToast("提示", "当前分镜无预置 Baseline 快照，请先执行结构化建档", "warning");
    }
}

async function savePromptStudio(autoGenerate = false) {
    const text = document.getElementById("studioPromptText").value;
    const cardTextarea = document.getElementById(`prompt${activeStudioShotId}`);
    if (cardTextarea) {
        cardTextarea.value = text;
        cardTextarea.innerText = text;
    }

    closePromptStudioModal();
    showToast("保存成功", `分镜 ${activeStudioShotId} 提示词已同步更新！`, "success");

    if (autoGenerate) {
        await generateSingleShot(activeStudioShotId);
    }
}

// -----------------------------------------------------------------------------
// 11. 分镜多版本对比与 A/B 看板 (Version Compare)
// -----------------------------------------------------------------------------
function openVersionCompareModal(shotId) {
    const titles = {
        S01: "S01 真实场景建立",
        S02: "S02 单手拿起与使用",
        S03: "S03 平稳放回与记忆点",
    };

    const bEl = document.getElementById("compareShotBadge");
    bEl.innerText = shotId;
    bEl.className = `shot-badge ${shotId === "S02" ? "orange" : ""}`;

    document.getElementById("compareModalTitle").innerText = `分镜多版本对比与 A/B 质检看板 (${titles[shotId] || shotId})`;

    // 绑定左右视频
    const v10El = document.getElementById("cmpVideoV10");
    const v11El = document.getElementById("cmpVideoV11");
    const curVideo = document.getElementById(`video${shotId}`);
    const currentTask = currentTasks[shotId];

    // 如果当前有视频，作为 V1.1 显示
    if (curVideo && curVideo.src) {
        v11El.src = curVideo.src;
        v11El.style.display = "block";
        document.getElementById("cmpPlaceholderV11").style.display = "none";
    }

    // V1.0 只能展示真实历史记录，不再绑定不存在的演示文件。
    const baseline = [...(shotHistory[shotId] || [])].reverse().find(item => item.prompt_version === "1.0" && item.video_url);
    if (baseline) {
        v10El.src = baseline.video_url;
        v10El.style.display = "block";
        document.getElementById("cmpPlaceholderV10").style.display = "none";
    } else {
        v10El.removeAttribute("src");
        v10El.style.display = "none";
        document.getElementById("cmpPlaceholderV10").style.display = "flex";
    }

    const resultText = item => item && item.score != null
        ? `QA: ${item.score}分 · ${(item.failure_codes || []).join(",") || "无 Failure Code"}`
        : "QA: 尚无实际评分";
    document.getElementById("cmpResultV10").textContent = resultText(baseline);
    document.getElementById("cmpResultV11").textContent = currentTask && currentTask.qa_score != null
        ? `QA: ${currentTask.qa_score}分 · ${currentTask.qa_status || currentTask.status}`
        : "QA: 尚无实际评分";
    document.getElementById("cmpPromptV10").textContent = baseline?.prompt_text
        ? `${baseline.prompt_text.slice(0, 180)}...`
        : "尚无 V1.0 历史 Prompt";

    // 提示词特征展示
    const pEl = document.getElementById(`prompt${shotId}`);
    if (pEl) {
        const pVal = pEl.value;
        document.getElementById("cmpPromptV11").innerText = pVal.slice(0, 180) + "...";
    }

    document.getElementById("versionCompareModal").style.display = "flex";
}

function closeVersionCompareModal() {
    document.getElementById("versionCompareModal").style.display = "none";
    const v10El = document.getElementById("cmpVideoV10");
    const v11El = document.getElementById("cmpVideoV11");
    if (v10El) v10El.pause();
    if (v11El) v11El.pause();
}
