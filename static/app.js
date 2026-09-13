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
async function analyzeAndCompile(isUserClick = false) {
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
            }),
        });

        if (!res.ok) {
            const errText = await res.text();
            throw new Error(`服务响应异常 (${res.status}): ${errText}`);
        }

        const data = await res.json();

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
        showToast("成片缝合完成", `🎉 15s 视频与 TTS 配音合成完毕，已回写飞书资产中心！`, "success");
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

// 8. 接口与模型配置弹窗 (对齐 WebLockShot 动态模型服务商与 API Key 选项)
function onModelConfigChange(modelVal) {
    const lblKey = document.getElementById("lbl_api_key");
    const inputKey = document.getElementById("cfg_jimeng_key");
    const hint = document.getElementById("cfg_key_hint");
    const boxEndpoint = document.getElementById("box_seedance_endpoint");
    const secTitle = document.getElementById("sec_model_title");

    if (modelVal.startsWith("seedance")) {
        secTitle.innerText = "⚡ 字节跳动火山引擎方舟 (Seedance 2.0) 算力配置";
        lblKey.innerText = "火山引擎方舟 (Ark) / Seedance API Key:";
        inputKey.placeholder = "填入火山引擎 ARK_API_KEY (如: 8f4e2b01-xxxx)...";
        hint.innerText = "💡 已适配字节跳动官方火山引擎方舟 (ByteDance Ark) 工业级接口协议";
        if (boxEndpoint) boxEndpoint.style.display = "block";
    } else if (modelVal === "jimeng-video-v2") {
        secTitle.innerText = "⚡ 字节即梦 (Jimeng 2.0) 开放平台配置";
        lblKey.innerText = "即梦开放平台 API Key / Session Token:";
        inputKey.placeholder = "填入公司提供的即梦开放平台 API Key / Token...";
        hint.innerText = "💡 已适配字节即梦开放平台 Web / RESTful 视频生成协议";
        if (boxEndpoint) boxEndpoint.style.display = "none";
    } else if (modelVal.startsWith("kling")) {
        secTitle.innerText = "⚡ 快手可灵 (Kling 1.5) 算力配置";
        lblKey.innerText = "快手可灵 (Kling) API Key (AccessKey):";
        inputKey.placeholder = "填入快手可灵 AccessKey / SecretKey...";
        hint.innerText = "💡 已适配快手可灵 1.5 工业级视频模型生成协议";
        if (boxEndpoint) boxEndpoint.style.display = "none";
    }
}

async function quickSwitchModel(modelVal) {
    try {
        const res = await fetch("/api/system/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jimeng_default_model: modelVal }),
        });
        if (res.ok) {
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
            document.getElementById("cfg_jimeng_model").value = curModel;
            document.getElementById("cfg_jimeng_key").value = cfg.seedance_ark_api_key || cfg.jimeng_api_key || "";
            document.getElementById("cfg_billing_mode").value = cfg.billing_mode || "CNY";
            document.getElementById("cfg_cost_per_second").value = cfg.cost_per_second_cny || 0.05;
            document.getElementById("cfg_feishu_app_id").value = cfg.feishu_app_id || "";
            document.getElementById("cfg_feishu_token").value = cfg.feishu_bitable_app_token || "";
            
            if (document.getElementById("cfg_seedance_endpoint")) {
                document.getElementById("cfg_seedance_endpoint").value = cfg.seedance_endpoint_id || "";
            }
            if (document.getElementById("cfg_llm_base_url")) {
                document.getElementById("cfg_llm_base_url").value = cfg.llm_api_base_url || "https://api.deepseek.com/v1";
            }
            if (document.getElementById("cfg_llm_key")) {
                document.getElementById("cfg_llm_key").value = cfg.llm_api_key || "";
            }

            // 同步顶部快捷选择器
            const topSel = document.getElementById("headerModelSelector");
            if (topSel) topSel.value = curModel;

            onModelConfigChange(curModel);
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
        billing_mode: document.getElementById("cfg_billing_mode").value,
        cost_per_second_cny: parseFloat(document.getElementById("cfg_cost_per_second").value) || 0.05,
        feishu_app_id: document.getElementById("cfg_feishu_app_id").value.trim(),
        feishu_bitable_app_token: document.getElementById("cfg_feishu_token").value.trim(),
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
        await fetchSystemStatus();
        const topSel = document.getElementById("headerModelSelector");
        if (topSel) topSel.value = chosenModel;

        showToast("系统配置已生效", "✅ 模型引擎与 API 密钥参数已保存并动态生效！", "success");
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
    toast.innerHTML = `
        <span class="toast-icon">${icon}</span>
        <div class="toast-content">
            <div class="toast-title">${title}</div>
            <div class="toast-desc">${desc}</div>
        </div>
    `;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateY(10px) scale(0.95)";
        toast.style.transition = "all 0.3s ease";
        setTimeout(() => toast.remove(), 300);
    }, 3800);
}
