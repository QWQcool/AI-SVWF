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
let currentImageModel = "doubao-seedream-5-0-260128";
let currentVisionModel = "glm-5-3-flash-260828";
let publicVirtualActorCatalog = null;
let currentVirtualActorGroupId = "";
let uploadedAssets = [];
let currentFirstFrames = { S01: null, S02: null, S03: null };
let currentFirstFramePromises = {};
let currentFirstFrameFailures = { S01: null, S02: null, S03: null };
let workspaceEpoch = 0;
let activeAnalysisRequestId = 0;
let activeQAShotId = "S01";
let activeStudioShotId = "S02";
let settingsInitialSnapshot = null;
let settingsPreviouslyFocusedElement = null;

const MANUAL_PAID_RETRY_CODES = new Set([
    "ARK_SUBMISSION_UNCERTAIN",
    "ARK_VISION_SUBMISSION_UNCERTAIN",
    "ARK_VISION_RESULT_REVIEW_REQUIRED",
    "ARK_IMAGE_SUBMISSION_UNCERTAIN",
    "ARK_IMAGE_ARCHIVE_FAILED",
    "ARK_IMAGE_ARCHIVE_SOURCE_UNAVAILABLE",
    "RESUME_REQUIRES_REVIEW",
]);

const LOCAL_ARCHIVE_RETRY_CODES = new Set(["ARK_IMAGE_ARCHIVE_FAILED"]);

function requiresManualPaidRetry(record) {
    if (isLocalArchiveRetry(record)) return false;
    return Boolean(record?.error_code && MANUAL_PAID_RETRY_CODES.has(record.error_code));
}

function isLocalArchiveRetry(record) {
    return Boolean(
        record?.remote_url
        && record?.error_code
        && LOCAL_ARCHIVE_RETRY_CODES.has(record.error_code)
    );
}

function videoRetryConfirmationKind({
    mockMode,
    skipCostConfirmation = false,
    priorTask = null,
    productId = null,
    version = "1.0",
    persistedReviewRequired = false,
}) {
    if (mockMode) return null;
    const priorSubmissionUncertain = priorTask?.product_id === productId
        && priorTask?.prompt_version === version
        && requiresManualPaidRetry(priorTask);
    if (persistedReviewRequired || priorSubmissionUncertain) return "manual-review";
    return skipCostConfirmation ? null : "cost";
}

function isAmbiguousVideoSubmissionFailure({
    mockMode,
    postStarted,
    submissionAcknowledged,
    responseStatus,
}) {
    return !mockMode
        && postStarted
        && !submissionAcknowledged
        && (responseStatus === undefined || responseStatus >= 500);
}

// 分镜版本历史栈 (参考 WebLockShot shotHistory 架构)
let shotHistory = {
    S01: [],
    S02: [],
    S03: [],
};

// 全局 Failure Code 目录缓存 (来自 /api/qa/failure-codes)
let failureCodesCatalog = [];
let qaOccurrenceDrafts = {};


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
    setupProductUploader();
    setupSettingsModalDismissal();
    await loadPublicVirtualActors();
    await fetchSystemStatus();
    await loadFailureCodesCatalog();
    // 优先恢复刷新前的持久化商品/任务，避免真实任务失联或重复扣费。
    const restored = await restoreWorkspace();
    if (!restored) {
        resetProductWorkflowState();
    }
    updateRepairButtonLabels();
});

function selectedVirtualActor() {
    return publicVirtualActorCatalog?.actors?.find(
        actor => actor.group_id === currentVirtualActorGroupId
    ) || null;
}

function renderVirtualActorDetails() {
    const details = document.getElementById("virtualActorDetails");
    if (!details) return;
    const actor = selectedVirtualActor();
    details.textContent = actor
        ? `${actor.country} · ${actor.gender} · ${actor.age}岁 · ${actor.role}｜${actor.description}`
        : "未使用固定公共虚拟人；真实视频将回到匿名人物兼容链路。";
}

async function loadPublicVirtualActors() {
    const selector = document.getElementById("virtualActorSelect");
    if (!selector) return;
    try {
        currentVirtualActorGroupId = localStorage.getItem("ai_svwf_virtual_actor_group_id") || "";
        const response = await fetch("/api/virtual-actors/public");
        const catalog = await readJsonOrThrow(response);
        publicVirtualActorCatalog = catalog;
        selector.replaceChildren();
        catalog.actors.forEach(actor => {
            const option = document.createElement("option");
            option.value = actor.group_id;
            option.textContent = `${actor.country} · ${actor.gender} · ${actor.age}岁 · ${actor.role}`;
            selector.appendChild(option);
        });
        const noneOption = document.createElement("option");
        noneOption.value = "";
        noneOption.textContent = "不使用固定虚拟人（兼容旧任务）";
        selector.appendChild(noneOption);

        const explicitlyDisabled = localStorage.getItem("ai_svwf_virtual_actor_disabled") === "1";
        const validSavedActor = catalog.actors.some(
            actor => actor.group_id === currentVirtualActorGroupId
        );
        currentVirtualActorGroupId = explicitlyDisabled
            ? ""
            : (validSavedActor ? currentVirtualActorGroupId : catalog.default_group_id);
        selector.value = currentVirtualActorGroupId;
        if (currentVirtualActorGroupId) {
            localStorage.setItem("ai_svwf_virtual_actor_group_id", currentVirtualActorGroupId);
        }
        renderVirtualActorDetails();
    } catch (error) {
        console.warn("公共虚拟人目录读取失败:", error);
        publicVirtualActorCatalog = null;
        currentVirtualActorGroupId = "";
        selector.replaceChildren();
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "人物目录暂不可用";
        selector.appendChild(option);
        renderVirtualActorDetails();
    }
}

async function persistProductVirtualActor(productId, groupId = currentVirtualActorGroupId) {
    if (!productId) return null;
    const response = await fetch(`/api/products/${encodeURIComponent(productId)}/virtual-actor`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_id: groupId || null }),
    });
    return readJsonOrThrow(response);
}

function clearActorDependentPreview() {
    workspaceEpoch += 1;
    currentTasks = { S01: null, S02: null, S03: null };
    currentFirstFrames = { S01: null, S02: null, S03: null };
    currentFirstFramePromises = {};
    currentFirstFrameFailures = { S01: null, S02: null, S03: null };
    ["S01", "S02", "S03"].forEach(shotId => {
        const video = document.getElementById(`video${shotId}`);
        if (video) {
            video.removeAttribute("src");
            video.removeAttribute("poster");
            video.style.display = "none";
            video.load();
        }
        const placeholder = document.querySelector(`#viewport${shotId} .empty-video-placeholder`);
        if (placeholder) placeholder.style.display = "flex";
        const status = document.getElementById(`status${shotId}`);
        if (status) {
            status.className = "status-tag";
            status.textContent = "待生成";
        }
    });
}

async function handleVirtualActorChange() {
    const selector = document.getElementById("virtualActorSelect");
    currentVirtualActorGroupId = selector?.value || "";
    if (currentVirtualActorGroupId) {
        localStorage.setItem("ai_svwf_virtual_actor_group_id", currentVirtualActorGroupId);
        localStorage.removeItem("ai_svwf_virtual_actor_disabled");
    } else {
        localStorage.removeItem("ai_svwf_virtual_actor_group_id");
        localStorage.setItem("ai_svwf_virtual_actor_disabled", "1");
    }
    renderVirtualActorDetails();
    clearActorDependentPreview();
    if (!currentProductId) return;
    try {
        await persistProductVirtualActor(currentProductId);
        const response = await fetch("/api/prompts/compile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                product_id: currentProductId,
                version: "1.0",
                provider: isMockMode ? "mock" : currentVideoProvider,
                model: isMockMode ? "mock-video-v1" : currentVideoModel,
                virtual_actor_group_id: currentVirtualActorGroupId || null,
            }),
        });
        fillPromptCards(await readJsonOrThrow(response));
        showToast(
            "公共虚拟人已更新",
            selectedVirtualActor() ? `当前锁定：${selectedVirtualActor().role}` : "已切回匿名人物兼容链路",
            "info",
        );
    } catch (error) {
        showToast("公共虚拟人更新失败", error.message, "danger");
    }
}

function resetProductWorkflowState({ clearAssets = false, clearPersistence = true } = {}) {
    workspaceEpoch += 1;
    currentProductId = null;
    currentTasks = { S01: null, S02: null, S03: null };
    currentFirstFrames = { S01: null, S02: null, S03: null };
    currentFirstFramePromises = {};
    currentFirstFrameFailures = { S01: null, S02: null, S03: null };
    shotHistory = { S01: [], S02: [], S03: [] };
    shotQAFailureCodes = { S01: [], S02: [], S03: [] };
    baselinePrompts = { S01: "", S02: "", S03: "" };
    if (clearAssets) uploadedAssets = [];
    if (clearPersistence) localStorage.removeItem("ai_svwf_current_product_id");
    ["S01", "S02", "S03"].forEach(shotId => {
        const video = document.getElementById(`video${shotId}`);
        if (video) {
            video.removeAttribute("src");
            video.removeAttribute("poster");
            video.style.display = "none";
            video.load();
        }
        const status = document.getElementById(`status${shotId}`);
        if (status) { status.className = "status-tag"; status.textContent = "待生成"; }
        const placeholder = document.querySelector(`#viewport${shotId} .empty-video-placeholder`);
        if (placeholder) placeholder.style.display = "flex";
        const version = document.getElementById(`ver${shotId}`);
        if (version) version.textContent = "V1.0";
    });
    const confBar = document.getElementById("confBar");
    if (confBar) { confBar.style.width = "0%"; confBar.style.backgroundColor = ""; }
    const confScore = document.getElementById("confScore");
    if (confScore) { confScore.textContent = "待分析"; confScore.style.color = ""; }
    const breakdownBox = document.getElementById("evidenceBreakdownBox");
    if (breakdownBox) breakdownBox.style.display = "none";
    const claimsContainer = document.getElementById("claimsContainer");
    if (claimsContainer) {
        const hint = document.createElement("div");
        hint.className = "claims-empty-hint";
        hint.textContent = "尚未建档分析，请在上方填写信息或上传图片后点击分析。";
        claimsContainer.replaceChildren(hint);
    }
    const riskAlert = document.getElementById("riskAlert");
    if (riskAlert) riskAlert.style.display = "none";
    updateRepairButtonLabels();
}

function analysisWorkspaceSnapshotsMatch(expected, current) {
    if (!expected || !current) return false;
    return expected.epoch === current.epoch
        && expected.productId === current.productId
        && expected.productName === current.productName
        && expected.productDesc === current.productDesc
        && expected.preferredScene === current.preferredScene
        && expected.productImageUrl === current.productImageUrl
        && expected.mockMode === current.mockMode
        && expected.videoProvider === current.videoProvider
        && expected.videoModel === current.videoModel
        && expected.imageModel === current.imageModel
        && expected.visionModel === current.visionModel
        && expected.virtualActorGroupId === current.virtualActorGroupId
        && JSON.stringify(expected.assetIds) === JSON.stringify(current.assetIds)
        && JSON.stringify(expected.assetUrls) === JSON.stringify(current.assetUrls);
}

function captureAnalysisWorkspaceSnapshot() {
    return {
        epoch: workspaceEpoch,
        productId: currentProductId,
        productName: document.getElementById("productName").value.trim(),
        productDesc: document.getElementById("productDesc").value.trim(),
        preferredScene: document.getElementById("preferredScene").value.trim(),
        productImageUrl: document.getElementById("productImageUrl").value.trim(),
        mockMode: isMockMode,
        videoProvider: currentVideoProvider,
        videoModel: currentVideoModel,
        imageModel: currentImageModel,
        visionModel: currentVisionModel,
        virtualActorGroupId: currentVirtualActorGroupId,
        assetIds: uploadedAssets.map(asset => asset.asset_id),
        assetUrls: uploadedAssets.map(asset => asset.url),
    };
}

function isAnalysisWorkspaceSnapshotCurrent(snapshot) {
    return analysisWorkspaceSnapshotsMatch(snapshot, captureAnalysisWorkspaceSnapshot());
}

function fillPromptCards(schema) {
    if (!schema?.shots || schema.shots.length < 3) return;
    schema.shots.forEach(shot => {
        const prompt = document.getElementById(`prompt${shot.shot_id}`);
        if (prompt) {
            prompt.value = shot.prompt;
            baselinePrompts[shot.shot_id] = shot.prompt;
        }
    });
}

function taskStatusPresentation(taskStatus) {
    switch (taskStatus) {
        case "CREATED":
        case "SUBMITTED":
        case "PROCESSING":
            return { className: "status-tag processing", text: "供应商处理中" };
        case "COMPLETED":
        case "QA_PENDING":
            return { className: "status-tag completed", text: "已生成 (待QA)" };
        case "PASS":
            return { className: "status-tag completed", text: "已生成 (QA通过)" };
        case "REPAIR":
            return { className: "status-tag warning", text: "已生成 (待修复)" };
        case "REJECTED":
            return { className: "status-tag failed", text: "已生成 (QA不通过)" };
        case "FAILED":
            return { className: "status-tag failed", text: "生成异常" };
        default:
            return { className: "status-tag", text: taskStatus || "待生成" };
    }
}

function renderTaskStatus(statusElement, taskStatus) {
    if (!statusElement) return;
    const presentation = taskStatusPresentation(taskStatus);
    statusElement.className = presentation.className;
    statusElement.textContent = presentation.text;
}

function hydrateTaskCard(task) {
    const shotId = task.shot_id;
    currentTasks[shotId] = task;
    shotQAFailureCodes[shotId] = task.failure_codes || [];
    const status = document.getElementById(`status${shotId}`);
    const version = document.getElementById(`ver${shotId}`);
    const meta = document.getElementById(`meta${shotId}`);
    const video = document.getElementById(`video${shotId}`);
    const prompt = document.getElementById(`prompt${shotId}`);
    const placeholder = document.querySelector(`#viewport${shotId} .empty-video-placeholder`);
    if (version) version.textContent = `V${task.prompt_version}`;
    if (meta) meta.textContent = `耗时: ${task.generation_time_seconds ?? "--"}s | 估算成本: ¥${task.estimated_cost ?? "--"}`;
    if (prompt && task.prompt_text) prompt.value = task.prompt_text;
    renderTaskStatus(status, task.status);
    if (video && task.video_url) {
        video.src = task.video_url;
        video.style.display = "block";
        if (placeholder) placeholder.style.display = "none";
        video.load();
    }
    updateRepairButtonLabels();
    if (["SUBMITTED", "PROCESSING"].includes(task.status)) {
        pollTaskResult(task.internal_task_id, shotId).catch(error => console.warn("恢复任务轮询失败", error));
    }
}

async function restoreWorkspace() {
    const productId = localStorage.getItem("ai_svwf_current_product_id");
    if (!productId) return false;
    try {
        const productResponse = await fetch(`/api/products/${encodeURIComponent(productId)}`);
        if (!productResponse.ok) throw new Error("saved product not found");
        let product = await productResponse.json();
        const storedActorGroupId = product.virtual_actor_group_id || product.virtual_actor?.group_id || "";
        if (storedActorGroupId && publicVirtualActorCatalog?.actors?.some(
            actor => actor.group_id === storedActorGroupId
        )) {
            currentVirtualActorGroupId = storedActorGroupId;
            localStorage.setItem("ai_svwf_virtual_actor_group_id", storedActorGroupId);
            localStorage.removeItem("ai_svwf_virtual_actor_disabled");
        } else if (currentVirtualActorGroupId) {
            product = await persistProductVirtualActor(product.product_id);
        }
        const actorSelector = document.getElementById("virtualActorSelect");
        if (actorSelector) actorSelector.value = currentVirtualActorGroupId;
        renderVirtualActorDetails();
        currentProductId = product.product_id;
        document.getElementById("productName").value = product.product_name || "";
        document.getElementById("productDesc").value = product.source_description || "";
        document.getElementById("preferredScene").value = product.preferred_scene || "";
        document.getElementById("productImageUrl").value = product.source_images?.[0] || "";
        renderAnalysisResult(product);
        if (product.source_asset_ids?.length) {
            const assetResponses = await Promise.all(
                product.source_asset_ids.map(assetId => fetch(`/api/assets/${encodeURIComponent(assetId)}`))
            );
            uploadedAssets = (await Promise.all(assetResponses.map(async response => (
                response.ok ? response.json() : null
            )))).filter(Boolean);
            renderUploadedAssets();
        }

        const compileResponse = await fetch("/api/prompts/compile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                product_id: product.product_id,
                version: "1.0",
                provider: isMockMode ? "mock" : currentVideoProvider,
                model: isMockMode ? "mock-video-v1" : currentVideoModel,
                virtual_actor_group_id: currentVirtualActorGroupId || null,
            }),
        });
        fillPromptCards(await readJsonOrThrow(compileResponse));

        const shotIds = ["S01", "S02", "S03"];
        const [tasksResponse, framesResponse, ...historyResponses] = await Promise.all([
            fetch(`/api/video/tasks?product_id=${encodeURIComponent(product.product_id)}`),
            fetch(`/api/images/tasks?product_id=${encodeURIComponent(product.product_id)}`),
            ...shotIds.map(shotId => fetch(
                `/api/products/${encodeURIComponent(product.product_id)}/shots/${encodeURIComponent(shotId)}/history`
            )),
        ]);
        const tasks = await readJsonOrThrow(tasksResponse);
        const frames = await readJsonOrThrow(framesResponse);
        const histories = await Promise.all(historyResponses.map(readJsonOrThrow));
        const activeExecutionMode = isMockMode ? "mock" : "real";
        shotIds.forEach((shotId, index) => {
            const frame = frames.find(item => item.shot_id === shotId
                && item.status === "COMPLETED"
                && (item.virtual_actor_group_id || "") === currentVirtualActorGroupId);
            if (frame) currentFirstFrames[shotId] = frame;
            const failedFrame = frames.find(item => item.shot_id === shotId
                && item.status === "FAILED"
                && (item.virtual_actor_group_id || "") === currentVirtualActorGroupId);
            if (failedFrame) currentFirstFrameFailures[shotId] = failedFrame;
            const eligibleTasks = tasks.filter(item => (
                item.shot_id === shotId
                && item.execution_mode === activeExecutionMode
                && (item.virtual_actor?.group_id || "") === currentVirtualActorGroupId
            ));
            const selectedTaskId = histories[index]?.selected_task_id || null;
            const task = eligibleTasks.find(item => item.internal_task_id === selectedTaskId)
                || eligibleTasks[0];
            if (task) hydrateTaskCard(task);
        });
        document.getElementById("uploadState").textContent = uploadedAssets.length
            ? `已恢复 ${uploadedAssets.length} 张商品素材与任务`
            : "已恢复上次商品档案与任务";
        showToast("工作区已恢复", `已恢复商品 ${product.product_name}，不会重新提交已有真实任务。`, "info");
        return true;
    } catch (error) {
        console.warn("Restore workspace failed:", error);
        localStorage.removeItem("ai_svwf_current_product_id");
        return false;
    }
}

function providerForModel(model) {
    if (!model || model === "mock-video-v1") return "mock";
    if (model.startsWith("kling")) return "kling";
    if (model.includes("seedance")) return "volcengine";
    return "volcengine";
}

async function readJsonOrThrow(response) {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
        const error = new Error(detail || `HTTP ${response.status}`);
        error.apiDetail = data.detail;
        error.responseStatus = response.status;
        throw error;
    }
    return data;
}

// 1. 获取系统运行状态与计费单价
async function fetchSystemStatus() {
    try {
        const res = await fetch("/api/system/status");
        const data = await res.json();
        isMockMode = data.mock_mode;
        if (data.ark && data.ark.video_model) {
            currentVideoModel = data.ark.video_model;
            currentImageModel = data.ark.image_model_primary || currentImageModel;
            currentVisionModel = data.ark.vision_model || currentVisionModel;
            currentVideoProvider = providerForModel(currentVideoModel);
            const topSelector = document.getElementById("headerModelSelector");
            if (topSelector) topSelector.value = currentVideoModel;
        }
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
                feishuStatus.innerText = "飞书凭据已认证";
                if (feishuDot) feishuDot.className = "pill-dot green";
                if (feishuPill) feishuPill.title = `四张表已配置；待同步队列 ${data.feishu.pending_feishu_sync ?? 0} 条，表级结果以队列状态为准`;
            } else {
                feishuStatus.innerText = "飞书配置完整·认证待验证";
                if (feishuDot) feishuDot.className = "pill-dot blue";
                if (feishuPill) feishuPill.title = "SQLite 仍为事实库；请检查飞书 App 权限、Token 与四张表 ID";
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
        if (!nextMode && !window.confirm("即将切换到真实 API 模式。生成首帧和视频会调用火山引擎并可能产生费用，是否继续？")) {
            return;
        }
        const res = await fetch("/api/system/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mock_mode: nextMode }),
        });
        const data = await res.json();
        isMockMode = data.current_mock_mode;
        updateModeBadge(isMockMode);
        if (localStorage.getItem("ai_svwf_current_product_id")) {
            resetProductWorkflowState({ clearAssets: false, clearPersistence: false });
            await restoreWorkspace();
        }
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

    resetProductWorkflowState({ clearAssets: true });
    renderUploadedAssets();
    document.getElementById("productImageUrl").value = "";

    document.getElementById("productName").value = preset.name;
    document.getElementById("productDesc").value = preset.desc;
    document.getElementById("preferredScene").value = preset.scene;

    event.target.classList.add("active");
    analyzeAndCompile();
}

// 3. 商品建档与 11 层 Prompt 编译
async function analyzeAndCompile(isUserClick = false) {
    const analysisRequestId = ++activeAnalysisRequestId;
    const initialSnapshot = captureAnalysisWorkspaceSnapshot();
    let guardedSnapshot = initialSnapshot;
    const btn = document.getElementById("btnAnalyze");
    btn.disabled = true;
    btn.innerHTML = initialSnapshot.assetIds.length
        ? "<span>⏳ GLM 正在识图并提取商品证据...</span>"
        : "<span>⏳ 正在进行合规审查与装配编译...</span>";

    const name = initialSnapshot.productName;
    const desc = initialSnapshot.productDesc;
    const scene = initialSnapshot.preferredScene;
    const imageUrl = initialSnapshot.productImageUrl;
    let activeVisionRetryKey = null;
    let activeVisionAttempt = 0;
    let visionRequestCompleted = false;

    try {
        // 第一步: 商品建档与合规分析
        if (!initialSnapshot.assetIds.length && !name) {
            throw new Error("请上传商品图片，或手工填写商品名称");
        }
        if (initialSnapshot.assetIds.length && isMockMode && !name) {
            throw new Error("Mock 模式不会调用 GLM 识图；请填写商品名称，或切换到真实 API 模式后识图");
        }
        const visionMode = initialSnapshot.assetIds.length > 0 && !isMockMode;
        const visionRetryKey = `vision:${initialSnapshot.assetIds.join("-")}`;
        const visionReviewKey = `${visionRetryKey}:requires-manual-review`;
        let visionAttempt = Number(sessionStorage.getItem(visionRetryKey) || "0");
        if (visionMode) {
            if (!isUserClick) {
                throw new Error("真实 GLM 识图只能由本机用户明确点击触发");
            }
            const needsManualReview = sessionStorage.getItem(visionReviewKey) === "1";
            const confirmation = needsManualReview
                ? "上一次 GLM 请求的计费/结果状态无法确认。请先在火山引擎控制台复核；若仍继续，将创建一个新的可能计费请求。确定继续吗？"
                : "将调用一次真实 GLM 视觉识别，可能产生费用。确定继续吗？";
            if (!window.confirm(confirmation)) {
                showToast("已取消真实识图", "没有提交新的 GLM 请求。", "info");
                return;
            }
            if (needsManualReview) {
                visionAttempt += 1;
                sessionStorage.setItem(visionRetryKey, String(visionAttempt));
                sessionStorage.removeItem(visionReviewKey);
            }
            activeVisionRetryKey = visionRetryKey;
            activeVisionAttempt = visionAttempt;
        }
        const analyzeRes = await fetch(visionMode ? "/api/products/analyze-vision" : "/api/products/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(visionMode ? {
                asset_ids: initialSnapshot.assetIds,
                product_name: name,
                short_description: desc,
                preferred_scene: scene,
                idempotency_key: `${visionRetryKey}:a${visionAttempt}`,
            } : {
                product_name: name,
                short_description: desc,
                preferred_scene: scene,
                product_images: initialSnapshot.assetUrls.length
                    ? initialSnapshot.assetUrls
                    : (imageUrl ? [imageUrl] : []),
            }),
        });
        let product = await readJsonOrThrow(analyzeRes);
        visionRequestCompleted = true;
        if (analysisRequestId !== activeAnalysisRequestId
            || !isAnalysisWorkspaceSnapshotCurrent(initialSnapshot)) {
            console.warn("Discarded stale product-analysis response after workspace/input change");
            return;
        }
        if (currentProductId && currentProductId !== product.product_id) {
            resetProductWorkflowState({ clearAssets: false });
        }
        currentProductId = product.product_id;
        localStorage.setItem("ai_svwf_current_product_id", product.product_id);
        if (currentVirtualActorGroupId) {
            product = await persistProductVirtualActor(product.product_id);
        }
        document.getElementById("productName").value = product.product_name || name;
        if (!desc && product.confirmed_information?.length) {
            document.getElementById("productDesc").value = product.confirmed_information.slice(0, 4).join("；");
        }
        if (product.source_images?.length) {
            document.getElementById("productImageUrl").value = product.source_images[0];
        }
        guardedSnapshot = captureAnalysisWorkspaceSnapshot();

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
                virtual_actor_group_id: currentVirtualActorGroupId || null,
            }),
        });
        const schema = await readJsonOrThrow(compileRes);
        if (analysisRequestId !== activeAnalysisRequestId
            || !isAnalysisWorkspaceSnapshotCurrent(guardedSnapshot)) {
            console.warn("Discarded stale prompt-compile response after workspace/input change");
            return;
        }

        // 填充三分镜的提示词预览 (支持 textarea.value 自由微调并存入 Baseline 快照)
        fillPromptCards(schema);

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
                visionMode ? "识图与 11 层 Prompt 编译成功" : "11层Prompt编译成功",
                `✅ 商品【${product.product_name}】建档完成（证据充分度 ${Number(product.evidence_sufficiency ?? product.information_confidence ?? 0).toFixed(2)}，${visionMode ? "GLM 图像证据" : "手工资料"}），3 个分镜已装配。`,
                "success"
            );
        }
    } catch (e) {
        if (activeVisionRetryKey && !visionRequestCompleted) {
            const retryAllowed = e.apiDetail?.retry_allowed === true;
            const submissionUncertain = e.apiDetail?.submission_uncertain === true
                || e.responseStatus === undefined
                || (e.responseStatus >= 500 && !e.apiDetail?.error_code);
            if (retryAllowed) {
                sessionStorage.setItem(activeVisionRetryKey, String(activeVisionAttempt + 1));
            } else if (submissionUncertain) {
                sessionStorage.setItem(`${activeVisionRetryKey}:requires-manual-review`, "1");
            }
        }
        if (analysisRequestId === activeAnalysisRequestId
            && isAnalysisWorkspaceSnapshotCurrent(guardedSnapshot)) {
            showToast("编译失败", e.message, "danger");
        }
    } finally {
        if (analysisRequestId === activeAnalysisRequestId) {
            btn.disabled = false;
            btn.innerHTML = "<span>🔎 识图并生成 11 层提示词</span>";
        }
    }
}

// 渲染合规与证据充分度卡片 (全面升级为证据充分度与结构化 Product Claims)
function renderAnalysisResult(product) {
    const confBar = document.getElementById("confBar");
    const confScore = document.getElementById("confScore");
    const riskAlert = document.getElementById("riskAlert");
    const riskContent = document.getElementById("riskContent");
    const breakdownBox = document.getElementById("evidenceBreakdownBox");
    const breakdownGrid = document.getElementById("breakdownGrid");
    const claimsContainer = document.getElementById("claimsContainer");
    const confirmedList = document.getElementById("confirmedList");
    const possibleList = document.getElementById("possibleList");

    const score = Number(product.evidence_sufficiency ?? product.information_confidence ?? 0);
    const percent = Math.max(0, Math.min(100, Math.round(score * 100)));
    confBar.style.width = `${percent}%`;

    if (score >= 0.85) {
        confBar.style.backgroundColor = "var(--color-success)";
        confScore.textContent = `${score.toFixed(2)} (高充分度·可信)`;
        confScore.style.color = "var(--color-success)";
    } else if (score >= 0.70) {
        confBar.style.backgroundColor = "var(--color-warning)";
        confScore.textContent = `${score.toFixed(2)} (中等充分度·部分推测)`;
        confScore.style.color = "var(--color-warning)";
    } else if (score >= 0.45) {
        confBar.style.backgroundColor = "var(--color-warning)";
        confScore.textContent = `${score.toFixed(2)} (低充分度·保守生成)`;
        confScore.style.color = "var(--color-warning)";
    } else {
        confBar.style.backgroundColor = "var(--color-danger)";
        confScore.textContent = `${score.toFixed(2)} (证据不足·仅纯展示/禁功效)`;
        confScore.style.color = "var(--color-danger)";
    }

    // 证据充分度核算明细 (Evidence Breakdown)
    if (product.evidence_breakdown && breakdownBox && breakdownGrid) {
        breakdownBox.style.display = "block";
        const bd = product.evidence_breakdown;
        const rawScore = bd.raw_model_score ?? bd.text_claim_base ?? score;
        const coverage = bd.source_coverage ?? bd.visual_cross_check ?? 0;
        const humanBonus = bd.human_bonus ?? bd.human_override_bonus ?? 0;
        const compPenalty = bd.compliance_penalty ?? (bd.risk_penalty != null ? Math.abs(bd.risk_penalty) : 0);
        const finalVal = bd.final_score ?? bd.final_sufficiency ?? score;

        const items = [
            { label: "模型基础分", val: Number(rawScore).toFixed(2), cls: "bd-positive" },
            { label: "多源/视觉覆盖", val: (coverage >= 0 ? "+" : "") + Number(coverage).toFixed(2), cls: "bd-positive" },
            { label: "人工确认补偿", val: (humanBonus >= 0 ? "+" : "") + Number(humanBonus).toFixed(2), cls: "bd-positive" },
            { label: "合规风控扣减", val: "-" + Number(compPenalty).toFixed(2), cls: compPenalty > 0 ? "bd-negative" : "" },
            { label: "综合核算充分度", val: Number(finalVal).toFixed(2), cls: "bd-highlight" },
        ];
        breakdownGrid.replaceChildren(...items.map(item => {
            const div = document.createElement("div");
            div.className = `breakdown-item ${item.cls}`;
            const lSpan = document.createElement("span");
            lSpan.className = "bd-label";
            lSpan.textContent = item.label;
            const vSpan = document.createElement("span");
            vSpan.className = "bd-val";
            vSpan.textContent = item.val;
            div.append(lSpan, vSpan);
            return div;
        }));
    } else if (breakdownBox) {
        breakdownBox.style.display = "none";
    }

    // 违规风险展示
    if (product.risk_information && product.risk_information.length > 0) {
        riskAlert.style.display = "block";
        riskContent.textContent = product.risk_information.join("\n");
    } else {
        riskAlert.style.display = "none";
    }

    // 结构化 Product Claims 证据溯源展示
    if (claimsContainer) {
        if (product.claims && product.claims.length > 0) {
            const sourceMap = {
                user_declaration: "用户声明",
                image_visible: "图像可见",
                packaging_text: "包装文字 (推测)",
                model_inference: "模型推论 (推测)",
                human_confirmation: "人工确认 (硬事实)",
            };
            const statusMap = {
                confirmed: { text: "已确认事实", cls: "status-confirmed" },
                possible: { text: "合理推测", cls: "status-possible" },
                rejected: { text: "已驳回/违规", cls: "status-rejected" },
            };

            claimsContainer.replaceChildren(...product.claims.map(claim => {
                const card = document.createElement("div");
                card.className = "claim-card";

                const topRow = document.createElement("div");
                topRow.className = "claim-top-row";

                const textSpan = document.createElement("span");
                textSpan.className = "claim-text";
                textSpan.textContent = claim.text || claim.claim_text || "";

                const srcType = claim.provenance?.[0]?.source_type || claim.evidence_source || "user_declaration";
                const sourceBadge = document.createElement("span");
                sourceBadge.className = `claim-source-badge source-${srcType}`;
                sourceBadge.textContent = sourceMap[srcType] || srcType;

                const st = claim.classification || claim.verification_status || "possible";
                const statusBadge = document.createElement("span");
                const sInfo = statusMap[st] || { text: st, cls: "" };
                statusBadge.className = `claim-status-badge ${sInfo.cls}`;
                statusBadge.textContent = sInfo.text;

                topRow.append(textSpan, sourceBadge, statusBadge);

                const actionRow = document.createElement("div");
                actionRow.className = "claim-action-row";

                const complianceCodes = claim.compliance_failure_codes || [];
                if (st === "possible" && complianceCodes.length === 0) {
                    const btnConfirm = document.createElement("button");
                    btnConfirm.className = "btn btn-xs btn-outline btn-claim-action";
                    btnConfirm.textContent = "✅ 人工确认为事实";
                    btnConfirm.onclick = () => confirmClaim(product.product_id, claim.claim_id, true);
                    actionRow.appendChild(btnConfirm);
                } else if (st === "rejected" || complianceCodes.length > 0) {
                    const blocked = document.createElement("span");
                    blocked.className = "claim-confirm-blocked";
                    blocked.textContent = complianceCodes.length
                        ? `⛔ ${complianceCodes.join(", ")} 需补资质证据，不能直接确认`
                        : "⛔ 已驳回卖点不能直接确认";
                    actionRow.appendChild(blocked);
                } else if (claim.human_confirmed || srcType === "human_confirmation" || claim.human_confirmed_by) {
                    const btnRevoke = document.createElement("button");
                    btnRevoke.className = "btn btn-xs btn-outline btn-claim-action text-warning";
                    btnRevoke.textContent = "↩ 撤销人工确认";
                    btnRevoke.onclick = () => confirmClaim(product.product_id, claim.claim_id, false);
                    actionRow.appendChild(btnRevoke);
                }

                card.append(topRow, actionRow);
                return card;
            }));
        } else {
            const emptyDiv = document.createElement("div");
            emptyDiv.className = "claims-empty-hint";
            emptyDiv.textContent = "未提取到卖点，请完善描述或上传商品图片后重新建档。";
            claimsContainer.replaceChildren(emptyDiv);
        }
    }

    // 兼容旧事实列表渲染
    if (confirmedList && product.confirmed_information) {
        confirmedList.replaceChildren(...product.confirmed_information.map(text => {
            const li = document.createElement("li"); li.textContent = text; return li;
        }));
    }
    if (possibleList && product.possible_information) {
        possibleList.replaceChildren(...product.possible_information.map(text => {
            const li = document.createElement("li"); li.textContent = text; return li;
        }));
    }
}

// 人工确认/撤销卖点
async function confirmClaim(productId, claimId, confirmed) {
    try {
        const res = await fetch(`/api/products/${encodeURIComponent(productId)}/claims/${encodeURIComponent(claimId)}/confirm`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                confirmed: confirmed,
                confirmed_by: "human_reviewer",
            }),
        });
        const updated = await readJsonOrThrow(res);
        renderAnalysisResult(updated);
        showToast("卖点确认已更新", confirmed ? "已将该卖点确认为事实并重新核算充分度" : "已撤销人工确认状态并重算充分度", "success");
    } catch (err) {
        showToast("卖点确认失败", err.message, "error");
    }
}

// 折叠提示词查看
function togglePromptAccordion(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.display = el.style.display === "none" ? "block" : "none";
}

// 4. 单镜头生成
async function ensureFirstFrame(shotId, version, promptText) {
    const requestedVirtualActorGroupId = currentVirtualActorGroupId;
    if (currentFirstFrames[shotId]
        && currentFirstFrames[shotId].product_id === currentProductId
        && currentFirstFrames[shotId].prompt_version === version
        && (currentFirstFrames[shotId].virtual_actor_group_id || "") === requestedVirtualActorGroupId) {
        return currentFirstFrames[shotId];
    }
    const requestedProductId = currentProductId;
    const promiseKey = `${requestedProductId}:${shotId}:${version}${requestedVirtualActorGroupId ? `:${requestedVirtualActorGroupId}` : ""}`;
    if (currentFirstFramePromises[promiseKey]) return currentFirstFramePromises[promiseKey];
    const retryStorageKey = `first-frame-attempt:${promiseKey}`;
    let attempt = Number(sessionStorage.getItem(retryStorageKey) || "0");
    const priorFailure = currentFirstFrameFailures[shotId];
    const retryingLocalArchive = priorFailure
        && priorFailure.product_id === requestedProductId
        && priorFailure.prompt_version === version
        && (priorFailure.virtual_actor_group_id || "") === requestedVirtualActorGroupId
        && isLocalArchiveRetry(priorFailure);
    if (retryingLocalArchive) {
        showToast(
            "恢复首帧归档",
            "正在复用同一 Seedream 生成结果重试本地归档，不调用新的图片模型。",
            "info",
        );
    }
    if (priorFailure
        && priorFailure.product_id === requestedProductId
        && priorFailure.prompt_version === version
        && (priorFailure.virtual_actor_group_id || "") === requestedVirtualActorGroupId
        && requiresManualPaidRetry(priorFailure)) {
        const proceed = window.confirm(
            "上一次首帧请求可能已计费，但结果未能安全归档。请先在火山引擎控制台复核；若继续，将创建新的付费首帧请求。确定继续吗？"
        );
        if (!proceed) throw new Error("已保留原幂等请求，未重复生成首帧");
        attempt += 1;
        sessionStorage.setItem(retryStorageKey, String(attempt));
        currentFirstFrameFailures[shotId] = null;
    }
    const promise = (async () => {
        try {
            const response = await fetch("/api/images/first-frame", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    product_id: requestedProductId,
                    shot_id: shotId,
                    prompt_version: version,
                    prompt: promptText,
                    asset_ids: uploadedAssets.map(asset => asset.asset_id),
                    model: currentImageModel,
                    size: "1440x2560",
                    idempotency_key: `${requestedProductId}:${shotId}:${version}:first-frame:a${attempt}`,
                    virtual_actor_group_id: requestedVirtualActorGroupId || null,
                }),
            });
            const frame = await readJsonOrThrow(response);
            if (frame.status !== "COMPLETED" || !frame.image_url) {
                currentFirstFrameFailures[shotId] = frame;
                const error = new Error(frame.error_message || "首帧生成失败");
                error.errorCode = frame.error_code;
                throw error;
            }
            if (frame.product_id !== currentProductId
                || currentVirtualActorGroupId !== requestedVirtualActorGroupId) {
                const staleError = new Error("商品或公共虚拟人已切换，已阻止旧首帧进入新任务");
                staleError.workspaceChanged = true;
                throw staleError;
            }
            currentFirstFrames[shotId] = frame;
            currentFirstFrameFailures[shotId] = null;
            const video = document.getElementById(`video${shotId}`);
            if (video) video.poster = frame.image_url;
            return frame;
        } catch (error) {
            if (error.workspaceChanged) throw error;
            const recordedFailure = currentFirstFrameFailures[shotId];
            const ambiguousTransportFailure = !recordedFailure
                && (error.responseStatus === undefined || error.responseStatus >= 500);
            if (ambiguousTransportFailure) {
                currentFirstFrameFailures[shotId] = {
                    product_id: requestedProductId,
                    prompt_version: version,
                    virtual_actor_group_id: requestedVirtualActorGroupId || null,
                    error_code: "ARK_IMAGE_SUBMISSION_UNCERTAIN",
                    error_message: error.message,
                };
            } else if (recordedFailure
                && !requiresManualPaidRetry(recordedFailure)
                && !isLocalArchiveRetry(recordedFailure)) {
                sessionStorage.setItem(retryStorageKey, String(attempt + 1));
            }
            throw error;
        }
    })();
    currentFirstFramePromises[promiseKey] = promise;
    try {
        return await promise;
    } finally {
        delete currentFirstFramePromises[promiseKey];
    }
}

async function generateSingleShot(shotId, version = "1.0", skipCostConfirmation = false) {
    const statusTag = document.getElementById(`status${shotId}`);
    const metaEl = document.getElementById(`meta${shotId}`);
    const viewport = document.getElementById(`viewport${shotId}`);
    const video = document.getElementById(`video${shotId}`);

    statusTag.className = "status-tag processing";
    statusTag.innerText = "生成中...";

    const promptEl = document.getElementById(`prompt${shotId}`);
    const promptText = (promptEl ? (promptEl.value || promptEl.innerText) : "").trim();
    const productName = document.getElementById("productName").value.trim();
    const requestedProductId = currentProductId;
    const requestedVirtualActorGroupId = currentVirtualActorGroupId;
    const requestEpoch = workspaceEpoch;
    const priorTask = currentTasks[shotId];
    const manualVideoRetryRequired = !isMockMode
        && priorTask?.product_id === requestedProductId
        && priorTask?.prompt_version === version
        && requiresManualPaidRetry(priorTask);
    const initialConfirmationKind = videoRetryConfirmationKind({
        mockMode: isMockMode,
        skipCostConfirmation,
        priorTask,
        productId: requestedProductId,
        version,
    });
    const initialConfirmation = initialConfirmationKind === "manual-review"
        ? `上一次 ${shotId} 视频提交状态无法确认。请先在火山引擎控制台复核；若继续，将创建新的可能计费任务。确定继续吗？`
        : `将为 ${shotId} 调用真实 Seedream/Seedance，可能产生费用。确定继续？`;
    if (initialConfirmationKind && !window.confirm(initialConfirmation)) {
        statusTag.className = "status-tag";
        statusTag.innerText = initialConfirmationKind === "manual-review" ? "需人工复核" : "待生成";
        return;
    }
    let manualRetryConfirmed = initialConfirmationKind === "manual-review";
    let videoAttemptStorageKey = null;
    let videoReviewStorageKey = null;
    let videoPostStarted = false;
    let videoSubmissionAcknowledged = false;

    try {
        if (!currentProductId) {
            throw new Error("请先完成商品识图/建档与 Prompt 编译");
        }
        let imageReference = document.getElementById("productImageUrl").value.trim();
        if (!isMockMode) {
            statusTag.innerText = "生成首帧...";
            const frame = await ensureFirstFrame(shotId, version, promptText);
            if (currentProductId !== requestedProductId
                || currentVirtualActorGroupId !== requestedVirtualActorGroupId
                || workspaceEpoch !== requestEpoch) {
                throw new Error("商品或公共虚拟人已切换，已取消旧视频提交");
            }
            // The locally archived frame does not expire; the adapter safely
            // converts this app URL into the data URL expected by Ark.
            imageReference = frame.image_url;
            statusTag.innerText = "视频生成中...";
        }
        const videoAttemptBase = `${requestedProductId}:${shotId}:${version}:${isMockMode ? "mock" : (currentFirstFrames[shotId]?.image_task_id || "real")}${requestedVirtualActorGroupId ? `:${requestedVirtualActorGroupId}` : ""}`;
        videoAttemptStorageKey = `video-attempt:${videoAttemptBase}`;
        videoReviewStorageKey = `video-review:${videoAttemptBase}`;
        let videoAttempt = Number(sessionStorage.getItem(videoAttemptStorageKey) || "0");
        const persistedReviewRequired = !isMockMode
            && sessionStorage.getItem(videoReviewStorageKey) === "1";
        const lateConfirmationKind = videoRetryConfirmationKind({
            mockMode: isMockMode,
            skipCostConfirmation: true,
            priorTask,
            productId: requestedProductId,
            version,
            persistedReviewRequired,
        });
        if (lateConfirmationKind === "manual-review" && !manualRetryConfirmed) {
            const proceed = window.confirm(
                `上一次 ${shotId} 视频 POST 的响应缺失或为 5xx，计费/任务状态无法确认。请先在火山引擎控制台复核；若继续，将创建新的可能计费任务。确定继续吗？`
            );
            if (!proceed) {
                statusTag.className = "status-tag failed";
                statusTag.innerText = "需人工复核";
                return;
            }
            manualRetryConfirmed = true;
        }
        const priorDefinitiveFailure = priorTask?.status === "FAILED"
            && !requiresManualPaidRetry(priorTask);
        const priorMatchesCurrentInput = priorTask?.product_id === requestedProductId
            && priorTask?.source_image === imageReference
            && priorTask?.prompt_version === version;
        if (persistedReviewRequired
            || (priorMatchesCurrentInput && (manualVideoRetryRequired || priorDefinitiveFailure))) {
            videoAttempt += 1;
            sessionStorage.setItem(videoAttemptStorageKey, String(videoAttempt));
        }
        if (persistedReviewRequired) sessionStorage.removeItem(videoReviewStorageKey);
        videoPostStarted = true;
        const res = await fetch("/api/video/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                product_id: requestedProductId || "PROD_DEMO",
                shot_id: shotId,
                prompt: promptText,
                prompt_version: version,
                product_name: productName,
                image_url: imageReference,
                provider: isMockMode ? "mock" : currentVideoProvider,
                model: isMockMode ? "mock-video-v1" : currentVideoModel,
                duration: 5,
                aspect_ratio: "9:16",
                idempotency_key: `${videoAttemptBase}:a${videoAttempt}`,
                virtual_actor_group_id: requestedVirtualActorGroupId || null,
            }),
        });
        const task = await readJsonOrThrow(res);
        videoSubmissionAcknowledged = true;
        currentTasks[shotId] = task;

        // 异步轮询任务结果
        await pollTaskResult(task.internal_task_id, shotId, requestedProductId, requestEpoch);
    } catch (e) {
        if (workspaceEpoch !== requestEpoch || currentProductId !== requestedProductId) return;
        const ambiguousSubmission = isAmbiguousVideoSubmissionFailure({
            mockMode: isMockMode,
            postStarted: videoPostStarted,
            submissionAcknowledged: videoSubmissionAcknowledged,
            responseStatus: e.responseStatus,
        });
        if (ambiguousSubmission && videoReviewStorageKey) {
            sessionStorage.setItem(videoReviewStorageKey, "1");
            statusTag.className = "status-tag failed";
            statusTag.innerText = "需人工复核";
            console.error(e);
            showToast(
                `${shotId} 提交状态待复核`,
                "POST 响应缺失或服务端返回 5xx；已保留当前幂等键，未自动创建新尝试。请先到火山引擎控制台确认。",
                "warning"
            );
            return;
        }
        statusTag.className = "status-tag failed";
        statusTag.innerText = "生成失败";
        console.error(e);
        showToast(`${shotId} 生成失败`, e.message, "danger");
    }
}

// 三镜预览：S01 + S02 + S03 各一条；正式 Round 1 是矩阵中的 3×3 共九条。
async function generateAllShots() {
    if (!isMockMode && !window.confirm("将提交 3 张真实 Seedream 首帧和 3 个 Seedance 视频任务，可能产生费用。是否继续？")) {
        return;
    }
    await Promise.all([
        generateSingleShot("S01", "1.0", true),
        generateSingleShot("S02", "1.0", true),
        generateSingleShot("S03", "1.0", true),
    ]);
}

// 轮询任务状态
async function pollTaskResult(
    taskId,
    shotId,
    expectedProductId = currentProductId,
    expectedEpoch = workspaceEpoch,
) {
    const statusTag = document.getElementById(`status${shotId}`);
    const metaEl = document.getElementById(`meta${shotId}`);
    const video = document.getElementById(`video${shotId}`);
    const placeholder = document.querySelector(`#viewport${shotId} .empty-video-placeholder`);

    const maxChecks = isMockMode ? 40 : 240;
    for (let i = 0; i < maxChecks; i++) {
        await new Promise(r => setTimeout(r, isMockMode ? 800 : 5000));
        const res = await fetch(`/api/video/tasks/${taskId}`);
        const task = await readJsonOrThrow(res);
        const activeTaskId = currentTasks[shotId]?.internal_task_id;
        if (workspaceEpoch !== expectedEpoch
            || currentProductId !== expectedProductId
            || task.product_id !== expectedProductId
            || (activeTaskId && activeTaskId !== taskId)) {
            const staleError = new Error("工作区或当前任务已切换，忽略旧任务轮询结果");
            staleError.workspaceChanged = true;
            throw staleError;
        }

        if (["COMPLETED", "QA_PENDING", "PASS", "REPAIR"].includes(task.status)) {
            currentTasks[shotId] = task;
            renderTaskStatus(statusTag, task.status);

            metaEl.innerText = `耗时: ${task.generation_time_seconds}s | 成本: ¥${task.estimated_cost}`;

            if (placeholder) placeholder.style.display = "none";
            video.src = task.video_url;
            video.style.display = "block";
            video.load();
            return task;
        } else if (task.status === "FAILED") {
            currentTasks[shotId] = task;
            statusTag.className = "status-tag failed";
            statusTag.innerText = "生成异常";
            showToast(`${shotId} 视频任务失败`, task.error_message || task.error_code || "供应商任务失败", "danger");
            throw new Error(task.error_message || task.error_code || "供应商任务失败");
        }
    }
    statusTag.className = "status-tag failed";
    statusTag.innerText = "轮询超时";
    showToast(`${shotId} 仍在生成`, "本地轮询已超时，可稍后刷新任务状态；服务端会继续轮询。", "warning");
    throw new Error("本地轮询超时，供应商任务仍由服务端继续跟踪");
}

// 5. 核心亮点: 针对任意失败分镜触发 V1.1 修复重跑 (Section 16, 17, 19 全镜头覆盖)
async function triggerRepair(shotId) {
    const oldTask = currentTasks[shotId];
    const requestedProductId = currentProductId;
    const requestEpoch = workspaceEpoch;
    const statusTag = document.getElementById(`status${shotId}`);
    const verTag = document.getElementById(`ver${shotId}`);

    if (!oldTask) {
        showToast("无法修复", "请先生成该分镜。", "warning");
        return;
    }
    if (oldTask.product_id !== currentProductId) {
        showToast("无法修复", "当前任务不属于正在编辑的商品，请刷新工作区。", "warning");
        return;
    }
    const activeCodes = shotQAFailureCodes[shotId] || oldTask.failure_codes || [];
    if (!activeCodes.length) {
        showToast("请先完成 QA", "修复必须依据人工 QA 记录的 Failure Code，系统不会伪装成自动缺陷识别。", "warning");
        return;
    }
    if (oldTask.execution_mode === "real" && !window.confirm("将依据人工 QA 的 Failure Code 提交 1 个真实 Seedance 修复任务并产生费用。确定继续？")) {
        return;
    }

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
        if (workspaceEpoch !== requestEpoch
            || currentProductId !== requestedProductId
            || newTask.product_id !== requestedProductId) {
            throw new Error("商品已切换，已忽略旧商品修复结果");
        }
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
        await pollTaskResult(newTask.internal_task_id, shotId, requestedProductId, requestEpoch);

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
        if (workspaceEpoch !== requestEpoch || currentProductId !== requestedProductId) return;
        showToast("修复重跑失败", e.message, "error");
    }
}

// 6. QA 质检评分面板交互与完整 Failure Code 标定 (Section 14 & 15)
async function loadFailureCodesCatalog() {
    try {
        const res = await fetch("/api/qa/failure-codes");
        if (res.ok) {
            failureCodesCatalog = await res.json();
            renderFailureChipsFromCatalog();
        }
    } catch (err) {
        console.warn("加载 Failure Code 目录失败，使用默认列表:", err);
    }
}

function renderFailureChipsFromCatalog() {
    const container = document.querySelector(".failure-chips");
    if (!container || !failureCodesCatalog.length) return;

    container.replaceChildren(...failureCodesCatalog.map(item => {
        const label = document.createElement("label");
        const isHard = item.kind === "hard_fail";
        label.className = `f-chip ${isHard ? "chip-hard-fail" : ""}`;
        label.title = `${item.category}: ${item.symptom}`;

        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = item.code;
        input.id = `fc_${item.code.toLowerCase().replace(/[^a-z0-9]/g, "_")}`;
        input.dataset.hard = isHard ? "true" : "false";
        input.onchange = () => {
            updateQASum();
            renderFailureOccurrenceEditors();
        };

        const text = document.createTextNode(` ${item.code} (${item.symptom.slice(0, 18)})`);
        label.append(input, text);

        if (isHard) {
            const badge = document.createElement("span");
            badge.className = "chip-hard-tag";
            badge.textContent = "HARD";
            label.appendChild(badge);
        }
        return label;
    }));
}

function openQAModal(shotId) {
    activeQAShotId = shotId;
    qaOccurrenceDrafts = {};
    document.getElementById("qaModalShotId").innerText = shotId;
    document.getElementById("qaModal").style.display = "flex";
    applyQAPreset("pass");
}

function closeQAModal() {
    document.getElementById("qaModal").style.display = "none";
}

function applyQAPreset(type) {
    const setVals = (c, p, a, h, pr, s, cam = 5, stable = 5, info = 5, compliance = 5) => {
        document.getElementById("in_consist").value = c;
        document.getElementById("in_person").value = p;
        document.getElementById("in_action").value = a;
        document.getElementById("in_hand").value = h;
        document.getElementById("in_prompt").value = pr;
        document.getElementById("in_scene").value = s;
        document.getElementById("in_camera").value = cam;
        document.getElementById("in_stability").value = stable;
        document.getElementById("in_info").value = info;
        document.getElementById("in_compliance").value = compliance;
    };

    document.querySelectorAll(".failure-chips input").forEach(cb => (cb.checked = false));

    if (type === "pass") {
        setVals(20, 15, 15, 10, 10, 10);
    } else if (type === "hand_fail") {
        setVals(14, 12, 10, 4, 8, 8);
        const handCb = document.querySelector(".failure-chips input[value='HAND001']");
        if (handCb) handCb.checked = true;
    } else if (type === "hard_fail") {
        setVals(5, 10, 8, 5, 5, 5);
        const hardCb = document.querySelector(".failure-chips input[value='HARD_FAIL_01']")
            || document.querySelector(".failure-chips input[value='PRO003']");
        if (hardCb) hardCb.checked = true;
    }
    updateQASum();
    renderFailureOccurrenceEditors();
}

function updateQASum() {
    const c = parseInt(document.getElementById("in_consist").value, 10);
    const p = parseInt(document.getElementById("in_person").value, 10);
    const a = parseInt(document.getElementById("in_action").value, 10);
    const h = parseInt(document.getElementById("in_hand").value, 10);
    const pr = parseInt(document.getElementById("in_prompt").value, 10);
    const s = parseInt(document.getElementById("in_scene").value, 10);
    const cam = parseInt(document.getElementById("in_camera").value, 10);
    const stable = parseInt(document.getElementById("in_stability").value, 10);
    const info = parseInt(document.getElementById("in_info").value, 10);
    const compliance = parseInt(document.getElementById("in_compliance").value, 10);

    document.getElementById("val_consist").innerText = c;
    document.getElementById("val_person").innerText = p;
    document.getElementById("val_action").innerText = a;
    document.getElementById("val_hand").innerText = h;
    document.getElementById("val_prompt").innerText = pr;
    document.getElementById("val_scene").innerText = s;
    document.getElementById("val_camera").innerText = cam;
    document.getElementById("val_stability").innerText = stable;
    document.getElementById("val_info").innerText = info;
    document.getElementById("val_compliance").innerText = compliance;

    // 检查是否有任何选中的 HARD FAIL
    let hasHardFail = false;
    document.querySelectorAll(".failure-chips input:checked").forEach(cb => {
        if (cb.dataset.hard === "true" || cb.value.startsWith("HARD_FAIL_")) {
            hasHardFail = true;
        }
    });

    const total = c + p + a + h + pr + s + cam + stable + info + compliance;
    document.getElementById("qaTotalScore").innerText = total;

    const pill = document.getElementById("qaStatusPill");
    if (hasHardFail) {
        pill.className = "qa-status-pill fail";
        pill.innerText = "FAIL (不合格·触发 HARD FAIL)";
    } else if (total >= 85) {
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

function renderFailureOccurrenceEditors() {
    const container = document.getElementById("failureOccurrenceEditors");
    if (!container) return;
    const checked = Array.from(document.querySelectorAll(".failure-chips input:checked"));
    if (checked.length === 0) {
        const empty = document.createElement("div");
        empty.className = "failure-occurrence-empty";
        empty.textContent = "勾选 Failure Code 后，可逐项填写自由备注、发生秒点和帧范围。";
        container.replaceChildren(empty);
        return;
    }

    container.replaceChildren(...checked.map(cb => {
        const code = cb.value;
        const item = failureCodesCatalog.find(entry => entry.code === code);
        const draft = qaOccurrenceDrafts[code] || {
            note: "",
            time_point_seconds: "",
            frame_start: "",
            frame_end: "",
        };
        qaOccurrenceDrafts[code] = draft;

        const card = document.createElement("div");
        card.className = "failure-occurrence-card";
        const title = document.createElement("div");
        title.className = "failure-occurrence-title";
        title.textContent = `${code} · ${item?.symptom || "缺陷定位"}`;

        const note = document.createElement("textarea");
        note.maxLength = 500;
        note.rows = 2;
        note.placeholder = "自由备注：描述画面中具体发生了什么";
        note.value = draft.note;
        note.oninput = () => { draft.note = note.value; };

        const fields = document.createElement("div");
        fields.className = "failure-occurrence-fields";
        const specs = [
            ["发生秒点", "0.0~5.0", "0.1", "time_point_seconds"],
            ["起始帧", "例如 24", "1", "frame_start"],
            ["结束帧", "例如 48", "1", "frame_end"],
        ];
        specs.forEach(([labelText, placeholder, step, key]) => {
            const label = document.createElement("label");
            label.textContent = labelText;
            const input = document.createElement("input");
            input.type = "number";
            input.min = "0";
            input.step = step;
            input.placeholder = placeholder;
            input.value = draft[key];
            input.oninput = () => { draft[key] = input.value; };
            label.appendChild(input);
            fields.appendChild(label);
        });
        card.append(title, note, fields);
        return card;
    }));
}

function optionalNumericValue(value, integer = false) {
    if (value === "" || value === null || value === undefined) return null;
    const parsed = integer ? parseInt(value, 10) : parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
}

async function submitQAResult() {
    const task = currentTasks[activeQAShotId];
    const taskId = task ? task.internal_task_id : "TASK_MANUAL";

    const failureCodes = [];
    const failureOccurrences = [];
    let hardFailCode = null;

    document.querySelectorAll(".failure-chips input:checked").forEach(cb => {
        const code = cb.value;
        failureCodes.push(code);
        const item = failureCodesCatalog.find(c => c.code === code);
        const isHard = cb.dataset.hard === "true" || (item && item.kind === "hard_fail") || code.startsWith("HARD_FAIL_");
        if (isHard && !hardFailCode && code.startsWith("HARD_FAIL_")) {
            hardFailCode = code;
        }
        const draft = qaOccurrenceDrafts[code] || {};
        failureOccurrences.push({
            code: code,
            note: (draft.note || "").trim(),
            time_point_seconds: optionalNumericValue(draft.time_point_seconds),
            frame_start: optionalNumericValue(draft.frame_start, true),
            frame_end: optionalNumericValue(draft.frame_end, true),
        });
    });

    const totalScore = parseInt(document.getElementById("qaTotalScore").innerText, 10);

    try {
        const response = await fetch(`/api/video/tasks/${taskId}/qa`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                internal_task_id: taskId,
                shot_id: activeQAShotId,
                score_product_consistency: parseInt(document.getElementById("in_consist").value, 10),
                score_person_realism: parseInt(document.getElementById("in_person").value, 10),
                score_action_naturalness: parseInt(document.getElementById("in_action").value, 10),
                score_hand_limb: parseInt(document.getElementById("in_hand").value, 10),
                score_prompt_following: parseInt(document.getElementById("in_prompt").value, 10),
                score_scene_realism: parseInt(document.getElementById("in_scene").value, 10),
                score_camera_rationality: parseInt(document.getElementById("in_camera").value, 10),
                score_frame_stability: parseInt(document.getElementById("in_stability").value, 10),
                score_info_accuracy: parseInt(document.getElementById("in_info").value, 10),
                score_compliance: parseInt(document.getElementById("in_compliance").value, 10),
                failure_codes: failureCodes,
                failure_notes: failureOccurrences.length
                    ? failureOccurrences.map(item => item.note || `人工标定缺陷: ${item.code}`)
                    : ["画面各维度达标"],
                failure_occurrences: failureOccurrences,
                hard_fail_code: hardFailCode,
            }),
        });
        const qaResult = await readJsonOrThrow(response);
        currentTasks[activeQAShotId] = {
            ...task,
            status: qaResult.task_status,
            qa_score: qaResult.qa_score,
            qa_status: qaResult.qa_status,
            failure_codes: failureCodes,
            failure_occurrences: failureOccurrences,
        };

        shotQAFailureCodes[activeQAShotId] = failureCodes;
        if (failureCodes.length > 0) {
            showToast("QA 缺陷打标已保存", `⚠️ 人工标记 ${activeQAShotId} 缺陷 [${failureCodes.join(', ')}]，已写入 SQLite；飞书已配置时会同步。`, "warning");
        } else {
            showToast("QA 质检达标", `✅ 分镜 ${activeQAShotId} 评分 ${totalScore} 分；结果已写入 SQLite，飞书已配置时会同步。`, "success");
        }
        closeQAModal();
        updateRepairButtonLabels();
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

    const productName = document.getElementById("productName").value.trim() || "带货商品";
    const productDesc = document.getElementById("productDesc").value.trim();
    const enableTts = document.getElementById("chkEnableTts") ? document.getElementById("chkEnableTts").checked : true;
    const voiceKey = document.getElementById("stitchVoiceSel") ? document.getElementById("stitchVoiceSel").value : "xiaoxiao";

    try {
        const shotIds = ["S01", "S02", "S03"];
        const histories = currentProductId
            ? await Promise.all(shotIds.map(async shotId => {
                const response = await fetch(
                    `/api/products/${encodeURIComponent(currentProductId)}/shots/${encodeURIComponent(shotId)}/history`
                );
                return readJsonOrThrow(response);
            }))
            : [];
        const taskIds = shotIds.map((shotId, index) => (
            histories[index]?.selected_task_id
            || currentTasks[shotId]?.internal_task_id
            || null
        )).filter(Boolean);
        if (taskIds.length !== 3) {
            throw new Error("S01、S02、S03 都必须先生成并选定可交付版本");
        }

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
            const ttsMeta = data.qa_pass_summary?.tts;
            ttsStatusEl.innerText = enableTts && ttsMeta?.available && !ttsMeta?.degraded
                ? `${ttsMeta.voice || voiceNames[voiceKey] || voiceKey} · 实际口播 3×5s 节拍对齐`
                : (enableTts ? "TTS 未通过交付校验" : "未启用 (仅无声拼接)");
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

    if (modelVal.includes("seedance")) {
        secTitle.innerText = "⚡ 字节跳动火山引擎方舟 (Doubao Seedance 2.0) 算力与成本配置";
        lblKey.innerText = "火山引擎方舟 (Ark) / Seedance API Key:";
        inputKey.placeholder = "填入火山引擎 ARK_API_KEY (如: 8f4e2b01-xxxx)...";
        hint.innerText = "💡 已接入 Ark 官方创建/查询接口；重复点击由本地幂等指纹拦截，重启后继续轮询";
        if (boxEndpoint) boxEndpoint.style.display = "block";
        defaultCost = 0.05;
        hintCostText = "💡 此处是本地估算参数，实际扣费以火山引擎账单为准";
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
            body: JSON.stringify({ jimeng_default_model: modelVal, video_model: modelVal }),
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
    settingsPreviouslyFocusedElement = document.activeElement;
    try {
        const res = await fetch("/api/system/settings");
        if (res.ok) {
            const cfg = await res.json();
            const curModel = cfg.video_model || cfg.jimeng_default_model || "doubao-seedance-2-0-260128";
            currentVideoModel = curModel;
            currentVideoProvider = providerForModel(curModel);
            document.getElementById("cfg_jimeng_model").value = curModel;
            document.getElementById("cfg_jimeng_key").value = "";
            document.getElementById("cfg_jimeng_key").placeholder = cfg.has_jimeng_key ? "已配置（留空保留现有密钥）" : "输入视频 Provider API Key";
            if (document.getElementById("cfg_vision_model_label")) {
                document.getElementById("cfg_vision_model_label").textContent = cfg.vision_model || currentVisionModel;
            }
            if (document.getElementById("cfg_image_model_label")) {
                document.getElementById("cfg_image_model_label").textContent = cfg.image_model_primary || currentImageModel;
            }
            document.getElementById("cfg_billing_mode").value = cfg.billing_mode || "CNY";
            document.getElementById("cfg_cost_per_second").value = cfg.cost_per_second_cny || 0.05;
            document.getElementById("cfg_feishu_app_id").value = cfg.feishu_app_id || "";
            const feishuTokenField = document.getElementById("cfg_feishu_token");
            feishuTokenField.value = "";
            feishuTokenField.type = "password";
            feishuTokenField.placeholder = cfg.has_feishu_app_token
                ? "已配置（留空保留现有 Token）"
                : "输入飞书 Bitable App Token";
            ["products", "tasks", "qa", "delivery"].forEach(name => {
                const field = document.getElementById(`cfg_feishu_table_${name}`);
                if (field) field.value = cfg[`feishu_table_${name}`] || "";
            });
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
            const arkKeyHint = document.getElementById("cfg_key_hint");
            if (arkKeyHint) {
                arkKeyHint.textContent = cfg.has_ark_key
                    ? "✅ Ark Key 已从 .env / 当前进程加载，无需每次填写；本页新值仅当前服务进程生效"
                    : "💡 尚未配置 Ark Key；长期配置请写入本机 .env，本页新值仅当前服务进程生效";
            }
            if (cfg.cost_per_second_cny !== undefined) {
                document.getElementById("cfg_cost_per_second").value = cfg.cost_per_second_cny;
            }
        }
    } catch (e) {
        console.warn("Load settings failed:", e);
    }
    const modal = document.getElementById("settingsModal");
    settingsInitialSnapshot = captureSettingsFormSnapshot();
    modal.style.display = "flex";
    modal.setAttribute("aria-hidden", "false");
    document.getElementById("btnCloseSettings")?.focus();
}

function captureSettingsFormSnapshot() {
    const modal = document.getElementById("settingsModal");
    if (!modal) return "";
    return JSON.stringify(Array.from(modal.querySelectorAll("input, select, textarea")).map(field => ({
        id: field.id,
        value: field.value,
        checked: field.checked,
    })));
}

function settingsHaveUnsavedChanges() {
    return settingsInitialSnapshot !== null
        && captureSettingsFormSnapshot() !== settingsInitialSnapshot;
}

function closeSettingsModal(force = false) {
    const modal = document.getElementById("settingsModal");
    if (!modal || modal.style.display === "none") return true;
    if (!force && settingsHaveUnsavedChanges()) {
        const shouldDiscard = window.confirm("接口配置有尚未保存的修改。确定关闭并放弃这些修改吗？");
        if (!shouldDiscard) return false;
    }

    modal.style.display = "none";
    modal.setAttribute("aria-hidden", "true");
    ["cfg_jimeng_key", "cfg_llm_key", "cfg_feishu_secret", "cfg_feishu_token"].forEach(id => {
        const field = document.getElementById(id);
        if (field) {
            field.value = "";
            field.type = "password";
        }
    });
    settingsInitialSnapshot = null;
    if (settingsPreviouslyFocusedElement?.isConnected) settingsPreviouslyFocusedElement.focus();
    settingsPreviouslyFocusedElement = null;
    return true;
}

function setupSettingsModalDismissal() {
    const modal = document.getElementById("settingsModal");
    if (!modal) return;
    modal.setAttribute("aria-hidden", "true");
    modal.addEventListener("click", event => {
        if (event.target === modal) closeSettingsModal();
    });
    document.addEventListener("keydown", event => {
        if (event.key !== "Escape" || modal.style.display === "none") return;
        event.preventDefault();
        closeSettingsModal();
    });
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
        video_model: chosenModel,
        jimeng_api_key: apiKey,
        ark_api_key: apiKey,
        seedance_ark_api_key: chosenModel.includes("seedance") ? apiKey : "",
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
        feishu_table_products: document.getElementById("cfg_feishu_table_products").value.trim(),
        feishu_table_tasks: document.getElementById("cfg_feishu_table_tasks").value.trim(),
        feishu_table_qa: document.getElementById("cfg_feishu_table_qa").value.trim(),
        feishu_table_delivery: document.getElementById("cfg_feishu_table_delivery").value.trim(),
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
        closeSettingsModal(true);
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
        hint.innerText = "💡 云端优先模式：提交飞书用于团队协同，同时保留 SQLite 与本地媒体作为故障安全底座。";
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

function setupProductUploader() {
    const zone = document.getElementById("productUploadZone");
    const input = document.getElementById("productImageFiles");
    if (!zone || !input) return;
    zone.addEventListener("click", () => input.click());
    zone.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") input.click();
    });
    input.addEventListener("change", () => uploadProductFiles([...input.files]));
    ["dragenter", "dragover"].forEach(type => zone.addEventListener(type, event => {
        event.preventDefault();
        zone.classList.add("dragover");
    }));
    ["dragleave", "drop"].forEach(type => zone.addEventListener(type, event => {
        event.preventDefault();
        zone.classList.remove("dragover");
    }));
    zone.addEventListener("drop", event => uploadProductFiles([...event.dataTransfer.files]));
}

async function uploadProductFiles(files) {
    const state = document.getElementById("uploadState");
    const available = Math.max(0, 6 - uploadedAssets.length);
    const selected = files.slice(0, available);
    if (!selected.length) {
        showToast("上传限制", "同一商品最多保留 6 张参考图。", "warning");
        return;
    }
    state.textContent = `正在校验并上传 ${selected.length} 张图片…`;
    const formData = new FormData();
    selected.forEach(file => formData.append("files", file));
    try {
        const response = await fetch("/api/assets/images", { method: "POST", body: formData });
        const assets = await readJsonOrThrow(response);
        resetProductWorkflowState({ clearAssets: false });
        document.querySelectorAll(".chip").forEach(chip => chip.classList.remove("active"));
        document.getElementById("productName").value = "";
        document.getElementById("productDesc").value = "";
        document.getElementById("preferredScene").value = "";
        const known = new Set(uploadedAssets.map(item => item.asset_id));
        assets.forEach(asset => {
            if (!known.has(asset.asset_id)) {
                uploadedAssets.push(asset);
                known.add(asset.asset_id);
            }
        });
        document.getElementById("productImageUrl").value = uploadedAssets[0]?.url || "";
        renderUploadedAssets();
        state.textContent = isMockMode
            ? `已载入 ${uploadedAssets.length} 张；Mock 模式请填写名称，真实模式可调用 GLM 识图`
            : `已载入 ${uploadedAssets.length} 张；点击下方按钮调用 GLM 识图并结构化建档`;
        showToast("商品图片已就绪", `已安全保存 ${assets.length} 张图片，本地素材会参与识图和首帧生成。`, "success");
    } catch (error) {
        state.textContent = "上传失败，请检查格式、尺寸和文件大小";
        showToast("商品图片上传失败", error.message, "danger");
    }
}

function renderUploadedAssets() {
    const grid = document.getElementById("assetPreviewGrid");
    grid.replaceChildren(...uploadedAssets.map(asset => {
        const box = document.createElement("div");
        box.className = "asset-preview";
        const image = document.createElement("img");
        image.src = asset.url;
        image.alt = asset.original_name;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.title = "从本次建档中移除";
        remove.addEventListener("click", event => {
            event.stopPropagation();
            uploadedAssets = uploadedAssets.filter(item => item.asset_id !== asset.asset_id);
            resetProductWorkflowState({ clearAssets: false });
            document.getElementById("productImageUrl").value = uploadedAssets[0]?.url || "";
            renderUploadedAssets();
            document.getElementById("uploadState").textContent = uploadedAssets.length
                ? `已载入 ${uploadedAssets.length} 张` : "未上传时仍可使用下方手工建档";
        });
        box.append(image, remove);
        return box;
    }));
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
        cell.textContent = `暂无${isMockMode ? "Mock" : "真实"}测试记录；运行 Round 1 后将在此显示同模式 SQLite 数据。`;
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
    const allRecords = await readJsonOrThrow(response);
    const currentExecutionMode = isMockMode ? "mock" : "real";
    matrixRecords = allRecords.filter(record => record.execution_mode === currentExecutionMode);
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
    const maxChecks = isMockMode ? 40 : 240;
    const intervalMs = isMockMode ? 800 : 5000;
    for (let i = 0; i < maxChecks; i++) {
        await new Promise(resolve => setTimeout(resolve, intervalMs));
        const response = await fetch(`/api/video/tasks/${taskId}`);
        const task = await readJsonOrThrow(response);
        if (!["CREATED", "SUBMITTED", "PROCESSING", "COMPLETED"].includes(task.status)) {
            if (task.status === "FAILED") {
                throw new Error(task.error_message || `${taskId} 生成失败`);
            }
            return task;
        }
    }
    throw new Error(`任务轮询超时: ${taskId}`);
}

async function runRound1MatrixTest() {
    if (!currentProductId) {
        showToast("无法运行 Round 1", "请先完成商品识别与 11 层提示词编译。", "danger");
        return;
    }
    if (!isMockMode) {
        const missingFrames = ["S01", "S02", "S03"].filter(shotId => !currentFirstFrames[shotId]?.image_url);
        if (missingFrames.length) {
            showToast("真实批测已拦截", `请先逐个生成 ${missingFrames.join("、")} 的合规首帧，再运行 9 条视频批测。`, "warning");
            return;
        }
        if (!window.confirm("Round 1 将提交 9 个真实 Seedance 视频任务，可能产生明显费用。确定继续？")) {
            return;
        }
    }
    const btn = document.getElementById("btnRunRound1");
    btn.disabled = true;
    btn.innerHTML = "<span>⏳ 正在并发执行 Round 1 (9条)...</span>";
    try {
        showToast("Round 1 启动", `正在生成受控 Prompt 变体和 9 条${isMockMode ? "Mock" : "真实"}任务记录；完成后仍需人工 QA。`, "info");
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
                    image_url: isMockMode
                        ? document.getElementById("productImageUrl").value.trim()
                        : currentFirstFrames[variant.shot_id].image_url,
                    duration: 5, aspect_ratio: "9:16",
                    product_name: document.getElementById("productName").value.trim(),
                    virtual_actor_group_id: currentVirtualActorGroupId || null,
                    idempotency_key: `${currentProductId}:round1:${variant.variant_id}:${isMockMode ? "mock" : currentFirstFrames[variant.shot_id].image_task_id}`,
                }),
            });
            return readJsonOrThrow(response);
        }));
        await Promise.all(submitted.map(task => waitForTaskRecord(task.internal_task_id)));
        await refreshMatrixRecords();
        renderMatrixTable();
        showToast("Round 1 生成完成", `9 条${isMockMode ? "Mock" : "真实"}记录已写入 SQLite，状态为待 QA；通过率将在人工评分后计算。`, "warning");
    } catch (error) {
        showToast("Round 1 失败", error.message, "danger");
    } finally {
        btn.disabled = false;
        btn.innerHTML = "<span>▶ 运行 Round 1 基准测试 (9条)</span>";
    }
}

async function runRound2OptimizationTest() {
    if (!isMockMode && !window.confirm("Section 19 最多会提交 3 个真实 Seedance 修复任务并产生费用。确定继续？")) {
        return;
    }
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
    const shotId = activeStudioShotId;
    const text = document.getElementById("studioPromptText").value.trim();
    if (!currentProductId || !text) {
        showToast("无法保存", "请先完成商品建档并填写提示词。", "warning");
        return;
    }
    if (autoGenerate && !isMockMode && !window.confirm(
        `将保存 ${shotId} 新 Prompt 版本并立即调用真实 Seedance，可能产生费用。确定继续？`
    )) {
        return;
    }
    try {
        const currentTask = currentTasks[shotId];
        const response = await fetch(
            `/api/products/${encodeURIComponent(currentProductId)}/shots/${encodeURIComponent(shotId)}/prompt-revisions`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    parent_revision_id: currentTask?.revision_id || null,
                    parent_task_id: currentTask?.internal_task_id || null,
                    prompt_text: text,
                    negative_prompt: currentTask?.negative_prompt || "",
                    change_note: "工坊模块微调",
                    generate_immediately: autoGenerate,
                    provider: isMockMode ? "mock" : currentVideoProvider,
                    model: isMockMode ? "mock-video-v1" : currentVideoModel,
                    image_url: currentTask?.source_image || currentFirstFrames[shotId]?.image_url || "",
                }),
            },
        );
        const result = await readJsonOrThrow(response);
        const revision = result.revision;
        const cardTextarea = document.getElementById(`prompt${shotId}`);
        if (cardTextarea) cardTextarea.value = revision.prompt_text;
        const versionTag = document.getElementById(`ver${shotId}`);
        if (versionTag) versionTag.textContent = `V${revision.version || revision.display_version}`;
        closePromptStudioModal();
        showToast("Prompt 版本已保存", `分镜 ${shotId} 已持久化为 V${revision.version || revision.display_version}。`, "success");
        if (autoGenerate && result.task) {
            hydrateTaskCard(result.task);
            await pollTaskResult(result.task.internal_task_id, shotId, currentProductId, workspaceEpoch);
        }
    } catch (error) {
        showToast("保存 Prompt 版本失败", error.message, "error");
    }
}

// -----------------------------------------------------------------------------
// 11. 分镜多版本对比与 A/B 看板 (Version Compare - 动态任意对比)
// -----------------------------------------------------------------------------
let currentCompareAttempts = [];

async function openVersionCompareModal(shotId) {
    const titles = {
        S01: "S01 真实场景建立",
        S02: "S02 单手拿起与使用",
        S03: "S03 平稳放回与记忆点",
    };

    const bEl = document.getElementById("compareShotBadge");
    if (bEl) {
        bEl.innerText = shotId;
        bEl.className = `shot-badge ${shotId === "S02" ? "orange" : ""}`;
    }

    const titleEl = document.getElementById("compareModalTitle");
    if (titleEl) {
        titleEl.innerText = `分镜多版本对比与 A/B 质检看板 (${titles[shotId] || shotId})`;
    }

    document.getElementById("versionCompareModal").style.display = "flex";
    await populateCompareSelects(shotId);
}

function closeVersionCompareModal() {
    document.getElementById("versionCompareModal").style.display = "none";
    const v10El = document.getElementById("cmpVideoV10");
    const v11El = document.getElementById("cmpVideoV11");
    if (v10El) v10El.pause();
    if (v11El) v11El.pause();
}

async function populateCompareSelects(shotId) {
    const selA = document.getElementById("cmpSelectA");
    const selB = document.getElementById("cmpSelectB");
    if (!selA || !selB) return;

    selA.replaceChildren();
    selB.replaceChildren();
    currentCompareAttempts = [];

    if (!currentProductId) {
        const opt = document.createElement("option");
        opt.textContent = "请先建档分析商品";
        selA.appendChild(opt);
        selB.appendChild(opt.cloneNode(true));
        return;
    }

    try {
        const res = await fetch(`/api/products/${encodeURIComponent(currentProductId)}/shots/${encodeURIComponent(shotId)}/history`);
        const data = await readJsonOrThrow(res);

        const attempts = [];
        (data.revisions || []).forEach(revNode => {
            (revNode.attempts || []).forEach(att => {
                att._revision = revNode.revision;
                attempts.push(att);
            });
        });

        currentCompareAttempts = attempts;

        if (attempts.length === 0) {
            const opt = document.createElement("option");
            opt.textContent = "暂无生成尝试";
            selA.appendChild(opt);
            selB.appendChild(opt.cloneNode(true));
            return;
        }

        attempts.forEach(att => {
            const label = `V${att.prompt_version} #${att.attempt_no} (${att.generation_kind} · ${att.status}${att.qa_score != null ? ` · ${att.qa_score}分` : ""})`;
            const optA = document.createElement("option");
            optA.value = att.internal_task_id;
            optA.textContent = label;
            selA.appendChild(optA);

            const optB = document.createElement("option");
            optB.value = att.internal_task_id;
            optB.textContent = label;
            selB.appendChild(optB);
        });

        // 默认 A 选第一项，B 选最后一项（最新优化版）
        selA.selectedIndex = 0;
        selB.selectedIndex = attempts.length > 1 ? attempts.length - 1 : 0;

        onCompareTaskSelect('A', selA.value);
        onCompareTaskSelect('B', selB.value);
    } catch (err) {
        console.warn("加载对比下拉列表失败:", err);
    }
}

function onCompareTaskSelect(target, taskId) {
    const att = currentCompareAttempts.find(a => a.internal_task_id === taskId);
    const isA = target === 'A';
    const videoEl = document.getElementById(isA ? "cmpVideoV10" : "cmpVideoV11");
    const placeholderEl = document.getElementById(isA ? "cmpPlaceholderV10" : "cmpPlaceholderV11");
    const resultEl = document.getElementById(isA ? "cmpResultV10" : "cmpResultV11");
    const promptEl = document.getElementById(isA ? "cmpPromptV10" : "cmpPromptV11");
    const badgeEl = document.getElementById(isA ? "cmpBadgeA" : "cmpBadgeB");
    const promptLabelEl = document.getElementById(isA ? "cmpPromptLabelA" : "cmpPromptLabelB");

    if (!att) {
        if (videoEl) { videoEl.removeAttribute("src"); videoEl.style.display = "none"; }
        if (placeholderEl) placeholderEl.style.display = "flex";
        if (resultEl) resultEl.textContent = "QA: 尚无实际评分";
        if (promptEl) promptEl.textContent = "无提示词数据";
        if (badgeEl) badgeEl.textContent = isA ? "任务 A" : "任务 B";
        if (promptLabelEl) promptLabelEl.textContent = `${isA ? "任务 A" : "任务 B"} 提示词:`;
        return;
    }

    const versionLabel = `V${att.prompt_version} · A${att.attempt_no}`;
    if (badgeEl) badgeEl.textContent = versionLabel;
    if (promptLabelEl) promptLabelEl.textContent = `${versionLabel} 提示词:`;
    if (placeholderEl) placeholderEl.textContent = `等待加载 ${versionLabel} 视频...`;

    if (videoEl && att.video_url) {
        videoEl.src = att.video_url;
        videoEl.style.display = "block";
        if (placeholderEl) placeholderEl.style.display = "none";
        videoEl.load();
    } else if (videoEl) {
        videoEl.removeAttribute("src");
        videoEl.style.display = "none";
        if (placeholderEl) placeholderEl.style.display = "flex";
    }

    const qaText = att.qa_score != null
        ? `QA: ${att.qa_score}分 · ${att.qa_status || att.status} ${att.failure_codes?.length ? `(${att.failure_codes.join(", ")})` : ""}`
        : `状态: ${att.status} · 暂无 QA 评分`;
    if (resultEl) resultEl.textContent = qaText;
    if (promptEl) promptEl.textContent = att.prompt_text ? `${att.prompt_text.slice(0, 180)}...` : "无提示词记录";
}

// -----------------------------------------------------------------------------
// 12. 不可变分镜历史看板 (Shot History & Version Tree)
// -----------------------------------------------------------------------------
let activeHistoryShotId = "S01";

function openShotHistoryModal(shotId) {
    activeHistoryShotId = shotId;
    const badge = document.getElementById("historyShotBadge");
    if (badge) badge.textContent = shotId;
    const title = document.getElementById("historyModalTitle");
    if (title) title.textContent = `分镜 ${shotId} 历史版本与生成尝试看板`;
    document.getElementById("shotHistoryModal").style.display = "flex";
    refreshShotHistory(shotId);
}

function closeShotHistoryModal() {
    document.getElementById("shotHistoryModal").style.display = "none";
}

function openABCompareFromHistory() {
    openVersionCompareModal(activeHistoryShotId);
}

async function refreshShotHistory(shotId) {
    const container = document.getElementById("historyTreeContainer");
    const selTag = document.getElementById("historySelectedTaskTag");
    const selNote = document.getElementById("historySelectedNote");

    if (!currentProductId) {
        container.replaceChildren();
        const empty = document.createElement("div");
        empty.className = "claims-empty-hint";
        empty.textContent = "请先建档分析商品后再查看历史看板。";
        container.appendChild(empty);
        return;
    }

    try {
        const res = await fetch(`/api/products/${encodeURIComponent(currentProductId)}/shots/${encodeURIComponent(shotId)}/history`);
        const data = await readJsonOrThrow(res);

        if (selTag) {
            selTag.textContent = data.selected_task_id ? `当前选用: ${data.selected_task_id}` : "未选择 (默认最新)";
        }
        if (selNote) {
            selNote.textContent = data.selection?.selection_note ? `(${data.selection.selection_note})` : "";
        }

        renderShotHistoryTree(data, shotId);
    } catch (err) {
        showToast("加载历史看板失败", err.message, "error");
    }
}

function renderShotHistoryTree(historyData, shotId) {
    const container = document.getElementById("historyTreeContainer");
    container.replaceChildren();

    if (!historyData.revisions || historyData.revisions.length === 0) {
        const empty = document.createElement("div");
        empty.className = "claims-empty-hint";
        empty.textContent = "该分镜暂无历史版本记录。点击【重新生成】或【手工修订 Prompt】可派生新版本。";
        container.appendChild(empty);
        return;
    }

    historyData.revisions.forEach(node => {
        const rev = node.revision;
        const attempts = node.attempts || [];

        const revCard = document.createElement("div");
        revCard.className = "history-rev-card";

        // Revision Header
        const revHeader = document.createElement("div");
        revHeader.className = "history-rev-header";

        const titleArea = document.createElement("div");
        titleArea.className = "rev-title-area";

        const vBadge = document.createElement("span");
        vBadge.className = "history-ver-tag";
        vBadge.textContent = `V${rev.version}`;

        const typeBadge = document.createElement("span");
        typeBadge.className = "history-type-badge";
        typeBadge.textContent = rev.change_type || "initial";

        const noteSpan = document.createElement("span");
        noteSpan.className = "history-rev-note";
        noteSpan.textContent = rev.change_note ? ` - ${rev.change_note}` : "";

        titleArea.append(vBadge, typeBadge, noteSpan);

        const timeSpan = document.createElement("span");
        timeSpan.className = "history-time-stamp";
        timeSpan.textContent = rev.created_at ? rev.created_at.slice(0, 19).replace("T", " ") : "";

        revHeader.append(titleArea, timeSpan);

        // Prompt accordion
        const promptWrap = document.createElement("div");
        promptWrap.className = "history-prompt-wrap";
        const promptToggle = document.createElement("div");
        promptToggle.className = "history-prompt-toggle";
        promptToggle.textContent = "▶ 展开该版本 11 层工业提示词";
        const promptContent = document.createElement("pre");
        promptContent.className = "history-prompt-content";
        promptContent.textContent = rev.prompt_text || "";
        promptContent.style.display = "none";
        promptToggle.onclick = () => {
            const isOpen = promptContent.style.display !== "none";
            promptContent.style.display = isOpen ? "none" : "block";
            promptToggle.textContent = isOpen ? "▶ 展开该版本 11 层工业提示词" : "▼ 收起提示词";
        };
        promptWrap.append(promptToggle, promptContent);

        // Attempts Grid
        const attWrap = document.createElement("div");
        attWrap.className = "history-attempts-wrap";

        if (attempts.length === 0) {
            const noAtt = document.createElement("div");
            noAtt.className = "no-attempts-hint";
            noAtt.textContent = "此版本尚未执行生成尝试。";
            attWrap.appendChild(noAtt);
        } else {
            attempts.forEach(att => {
                const attCard = document.createElement("div");
                const isSelected = historyData.selected_task_id === att.internal_task_id;
                attCard.className = `history-attempt-card ${isSelected ? "is-selected" : ""}`;

                const attTop = document.createElement("div");
                attTop.className = "att-top-row";

                const noTag = document.createElement("span");
                noTag.className = "att-no-tag";
                noTag.textContent = `Attempt #${att.attempt_no}`;

                const kindTag = document.createElement("span");
                kindTag.className = "att-kind-tag";
                kindTag.textContent = att.generation_kind || "initial";

                const statusTag = document.createElement("span");
                const pres = taskStatusPresentation(att.status);
                statusTag.className = pres.className;
                statusTag.textContent = pres.text;

                attTop.append(noTag, kindTag, statusTag);

                // Video thumbnail
                const mediaBox = document.createElement("div");
                mediaBox.className = "att-media-box";
                if (att.video_url) {
                    const v = document.createElement("video");
                    v.src = att.video_url;
                    v.controls = true;
                    v.playsInline = true;
                    v.className = "att-mini-video";
                    mediaBox.appendChild(v);
                } else {
                    const noV = document.createElement("div");
                    noV.className = "att-no-video";
                    noV.textContent = att.status === "FAILED" ? "生成失败" : "无视频";
                    mediaBox.appendChild(noV);
                }

                // Info row
                const infoRow = document.createElement("div");
                infoRow.className = "att-info-row";
                const scoreText = att.qa_score != null ? `QA: ${att.qa_score}分 (${att.qa_status || "已评"})` : "QA: 未评分";
                const codesText = att.failure_codes?.length ? `[${att.failure_codes.join(", ")}]` : "";
                infoRow.textContent = `${scoreText} ${codesText}`;

                const qaHistory = historyData.qa_records?.[att.internal_task_id] || [];
                const qaHistoryDetails = document.createElement("details");
                qaHistoryDetails.className = "att-qa-history";
                const qaSummary = document.createElement("summary");
                qaSummary.textContent = `QA 历史 ${qaHistory.length} 次`;
                qaHistoryDetails.appendChild(qaSummary);
                qaHistory.forEach((record, recordIndex) => {
                    const line = document.createElement("div");
                    const input = record.input || {};
                    const occurrences = input.failure_occurrences || [];
                    const occurrenceText = occurrences.map(item => {
                        const location = [
                            item.time_point_seconds == null ? "" : `${item.time_point_seconds}s`,
                            item.frame_start == null ? "" : `帧${item.frame_start}-${item.frame_end ?? item.frame_start}`,
                        ].filter(Boolean).join(" / ");
                        return `${item.code}${location ? ` @ ${location}` : ""}${item.note ? `：${item.note}` : ""}`;
                    }).join("；") || "无 Failure Code";
                    line.textContent = `#${recordIndex + 1} ${record.created_at || ""} · ${occurrenceText}`;
                    qaHistoryDetails.appendChild(line);
                });

                // Actions row
                const actionsRow = document.createElement("div");
                actionsRow.className = "att-actions-row";

                if (!isSelected) {
                    const btnSelect = document.createElement("button");
                    btnSelect.className = "btn btn-xs btn-success";
                    btnSelect.textContent = "⭐️ 设为选用";
                    btnSelect.onclick = () => selectShotTask(shotId, att.internal_task_id, rev.version, `选用 V${rev.version} #${att.attempt_no}`);
                    actionsRow.appendChild(btnSelect);
                } else {
                    const selectedBadge = document.createElement("span");
                    selectedBadge.className = "selected-pill";
                    selectedBadge.textContent = "✅ 当前选用";
                    actionsRow.appendChild(selectedBadge);
                }

                const btnReroll = document.createElement("button");
                btnReroll.className = "btn btn-xs btn-outline";
                btnReroll.textContent = "🎲 重抽";
                btnReroll.onclick = () => rerollShotTask(att.internal_task_id, shotId);
                actionsRow.appendChild(btnReroll);

                const btnQa = document.createElement("button");
                btnQa.className = "btn btn-xs btn-qa";
                btnQa.textContent = "QA 评分";
                btnQa.onclick = () => {
                    activeQAShotId = shotId;
                    currentTasks[shotId] = att;
                    openQAModal(shotId);
                };
                actionsRow.appendChild(btnQa);

                attCard.append(attTop, mediaBox, infoRow, qaHistoryDetails, actionsRow);
                attWrap.appendChild(attCard);
            });
        }

        revCard.append(revHeader, promptWrap, attWrap);
        container.appendChild(revCard);
    });
}

async function selectShotTask(shotId, taskId, version, note) {
    try {
        const res = await fetch(`/api/products/${encodeURIComponent(currentProductId)}/shots/${encodeURIComponent(shotId)}/selection`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                selected_task_id: taskId,
                selection_note: note || "人工看板选用",
            }),
        });
        await readJsonOrThrow(res);

        const taskRes = await fetch(`/api/video/tasks/${encodeURIComponent(taskId)}`);
        if (taskRes.ok) {
            const taskData = await taskRes.json();
            hydrateTaskCard(taskData);
        }
        await refreshShotHistory(shotId);
        showToast("版本选用已锁定", `分镜 ${shotId} 最终成片版本已锁定为 ${taskId}`, "success");
    } catch (err) {
        showToast("选用版本失败", err.message, "error");
    }
}

// -----------------------------------------------------------------------------
// 13. 同词重抽 (Reroll)
// -----------------------------------------------------------------------------
async function rerollShot(shotId) {
    const task = currentTasks[shotId];
    if (!task) {
        showToast("无法重抽", "该分镜尚未生成，请先生成初始分镜。", "warning");
        return;
    }
    await rerollShotTask(task.internal_task_id, shotId);
}

async function rerollShotTask(taskId, shotId) {
    const requestedProductId = currentProductId;
    const requestEpoch = workspaceEpoch;
    const statusTag = document.getElementById(`status${shotId}`);
    if (statusTag) {
        statusTag.className = "status-tag processing";
        statusTag.textContent = "同词重抽中...";
    }
    try {
        const res = await fetch(`/api/video/tasks/${encodeURIComponent(taskId)}/reroll`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
        });
        const newTask = await readJsonOrThrow(res);
        if (workspaceEpoch !== requestEpoch || currentProductId !== requestedProductId) {
            throw new Error("商品已切换，已忽略旧任务重抽");
        }
        hydrateTaskCard(newTask);
        showToast("重抽已发起", `分镜 ${shotId} 已发起第 #${newTask.attempt_no} 次尝试，复用版本 V${newTask.prompt_version}`, "info");
        await pollTaskResult(newTask.internal_task_id, shotId, requestedProductId, requestEpoch);
        if (document.getElementById("shotHistoryModal").style.display !== "none") {
            await refreshShotHistory(shotId);
        }
        updateRepairButtonLabels();
    } catch (err) {
        if (workspaceEpoch !== requestEpoch || currentProductId !== requestedProductId) return;
        showToast("同词重抽失败", err.message, "error");
    }
}

// -----------------------------------------------------------------------------
// 14. 手工修订 Prompt 派生新不可变版本
// -----------------------------------------------------------------------------
function openCreateRevisionModal() {
    const shotId = activeHistoryShotId;
    const currentPromptEl = document.getElementById(`prompt${shotId}`);
    document.getElementById("manualRevisionPrompt").value = currentPromptEl ? currentPromptEl.value : "";
    document.getElementById("manualRevisionNote").value = "";
    document.getElementById("manualRevisionNegative").value = "";
    document.getElementById("createRevisionModal").style.display = "flex";
}

function closeCreateRevisionModal() {
    document.getElementById("createRevisionModal").style.display = "none";
}

async function submitCreateRevision() {
    const shotId = activeHistoryShotId;
    const promptText = document.getElementById("manualRevisionPrompt").value.trim();
    const note = document.getElementById("manualRevisionNote").value.trim() || "人工手动微调";
    const neg = document.getElementById("manualRevisionNegative").value.trim();
    const autoGen = document.getElementById("manualRevisionAutoGenerate").checked;

    if (!promptText) {
        showToast("提示词不能为空", "请输入 11 层工业提示词内容", "warning");
        return;
    }
    if (autoGen && !isMockMode && !window.confirm(
        `将创建 ${shotId} 新 Prompt 版本并立即调用真实 Seedance，可能产生费用。确定继续？`
    )) {
        return;
    }

    try {
        const res = await fetch(`/api/products/${encodeURIComponent(currentProductId)}/shots/${encodeURIComponent(shotId)}/prompt-revisions`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                parent_revision_id: currentTasks[shotId]?.revision_id || null,
                parent_task_id: currentTasks[shotId]?.internal_task_id || null,
                prompt_text: promptText,
                negative_prompt: neg,
                change_type: "manual_revision",
                change_note: note,
                generate_immediately: autoGen,
                provider: isMockMode ? "mock" : currentVideoProvider,
                model: isMockMode ? "mock-video-v1" : currentVideoModel,
            }),
        });
        const resJson = await readJsonOrThrow(res);
        const revData = resJson.revision || resJson;
        const verStr = revData.version || revData.display_version || "1.1";

        const cardPrompt = document.getElementById(`prompt${shotId}`);
        if (cardPrompt) cardPrompt.value = revData.prompt_text || promptText;
        const cardVer = document.getElementById(`ver${shotId}`);
        if (cardVer) cardVer.textContent = `V${verStr}`;

        closeCreateRevisionModal();
        showToast("新版本已创建", `已派生新不可变版本 V${verStr} (${note})`, "success");

        if (autoGen && resJson.task) {
            hydrateTaskCard(resJson.task);
            pollTaskResult(
                resJson.task.internal_task_id,
                shotId,
                currentProductId,
                workspaceEpoch,
            );
        }
        await refreshShotHistory(shotId);
        updateRepairButtonLabels();
    } catch (err) {
        showToast("创建新版本失败", err.message, "error");
    }
}

// -----------------------------------------------------------------------------
// 15. 动态修复重跑按钮文案核算
// -----------------------------------------------------------------------------
function getNextVersion(currentVer) {
    try {
        const clean = String(currentVer || "1.0").replace(/^[Vv]/, "").trim();
        const match = clean.match(/^(\d+)\.(\d+)(.*)$/);
        if (!match) return "1.1";
        return `${parseInt(match[1], 10)}.${parseInt(match[2], 10) + 1}${match[3]}`;
    } catch (e) {
        return "1.1";
    }
}

function updateRepairButtonLabels() {
    ["S01", "S02", "S03"].forEach(shotId => {
        const btn = document.getElementById(`btnRepair${shotId}`);
        if (!btn) return;
        const task = currentTasks[shotId];
        if (!task) {
            btn.textContent = "🛠️ 修复重跑";
            btn.title = "生成分镜并由 QA 标记 Failure Code 后可触发修复重跑";
            return;
        }
        const nextVer = getNextVersion(task.prompt_version);
        btn.textContent = `🛠️ 生成 V${nextVer} 修复重跑`;
        btn.title = `依据已记录的 Failure Code 针对性修复并生成新版本 V${nextVer}`;
    });
}
