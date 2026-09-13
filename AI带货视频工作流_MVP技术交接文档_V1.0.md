# AI带货视频工作流 MVP 技术交接文档 V1.0

> 用途：AIGC内容岗与工作流/后端技术联调  
> 当前阶段：PoC / MVP技术验证  
> 核心目标：验证“用户仅上传基础商品素材，也能自动跑出可用带货视频”的最小闭环  
> 测试日期：2026-09-12

---

# 1. 本轮测试目标

本轮**不做完整平台**，不追求多品类、多模型、大规模并发，也不追求一次生成完美商用成片。

只验证下面这条最小闭环能否真实跑通：

```text
用户上传基础商品素材
↓
商品信息识别
↓
生成内部结构化商品档案
↓
选择视频策略
↓
生成15秒视频结构
↓
拆成3个5秒镜头
↓
按模块组装Prompt
↓
调用视频模型API
↓
获取生成结果
↓
QA评分
↓
记录Failure Code
↓
针对失败镜头生成V1.1 Prompt
↓
只重跑失败镜头
↓
拼接15秒成片
```

本轮技术验证成功的核心不是“偶尔出一条好视频”，而是：

1. 输入可以标准化；
2. Prompt可以自动组装；
3. 视频API能够正常调用；
4. 生成结果可以回写；
5. 失败可以被明确分类；
6. Prompt可以版本化；
7. 失败镜头可以单独重跑；
8. 第二轮优化后成功率可以提升。

---

# 2. 本轮测试范围

严格限制第一轮测试范围：

```text
商品数量：1个
视频类型：真实场景体验型
视频比例：9:16
总时长：15秒
生成方式：3 × 5秒
人物数量：1人
商品数量：1件
场景数量：1个
视频模型：先接1个，闭环跑通后再做第二模型A/B
```

本轮暂不做：

- 向量数据库；
- 多商品批量；
- 多视频类型；
- 自动爆款复刻；
- 复杂字幕；
- 视频模型生成长文字；
- 多模型自动路由；
- 自动投放；
- 大规模并发；
- 日产千条压力测试。

---

# 3. 用户侧最小输入

平台未来主打“低门槛”，因此不要求普通用户先整理复杂商品资料包。

第一阶段用户侧最小输入：

```json
{
  "product_name": "测试商品名称",
  "product_images": [
    "商品正面图URL"
  ]
}
```

可选字段：

```json
{
  "short_description": "",
  "reference_video": "",
  "target_audience": "",
  "preferred_scene": ""
}
```

原则：

- 用户输入尽量少；
- 系统内部自动结构化；
- 用户资料不足时不允许AI编造功效、参数、检测数据等事实。

---

# 4. 内部商品识别节点

节点建议命名：

```text
PRODUCT_ANALYSIS
```

输入：

```text
商品图片
+
商品名称
+
用户可选描述
```

最低输出结构：

```json
{
  "product_name": "",
  "brand": "",
  "category": "",
  "appearance_description": "",
  "confirmed_information": [],
  "possible_information": [],
  "usage_scenes": [],
  "risk_information": [],
  "information_confidence": 0.0
}
```

## 4.1 信息分类规则

### confirmed_information

只允许放：

- 从商品图明确识别的信息；
- 用户明确提供的信息；
- 可信资料中明确存在的信息。

### possible_information

只允许放：

- AI根据品类、外观、普通使用逻辑做出的可能性判断。

这些内容**不能自动当成事实卖点**。

### 禁止行为

禁止AI自动生成：

- 未提供的检测数据；
- 未提供的成分参数；
- 未提供的医疗功效；
- 未提供的收益承诺；
- 未验证的“第一、最好、100%”等绝对化表达。

---

# 5. 商品信息可信度

建议内部增加：

```text
information_confidence
```

规则：

```text
0.90–1.00
高可信，可正常进入生成流程

0.70–0.89
可以生成，但避免强事实性宣传

0.50–0.69
保守生成，只做普通展示与场景使用

<0.50
只做商品展示，不生成具体功效型文案
```

---

# 6. 15秒视频模板

模板ID：

```text
TPL_SCENE_PRODUCT_15S_V1
```

模板名称：

```text
真实场景体验型带货视频
```

生成策略：

```text
3 × 5秒
```

不建议第一轮直接让视频模型生成整条15秒。

---

## 6.1 S01｜0–5秒｜真实场景建立

目标：

```text
人物真实性
+
商品首帧一致性
+
场景真实性
```

结构：

- 第一帧人物已经处于真实生活/办公场景；
- 商品已经自然存在于合理位置；
- 人物不立刻拿商品；
- 人物继续当前正常活动；
- 不持续盯镜头；
- 不进行复杂手部动作；
- 固定机位或轻微手机手持感。

示例：

```text
女性坐在办公桌前操作电脑，
商品自然放在右手边桌面，
人物自然眨眼、移动鼠标、轻微调整坐姿。
```

---

## 6.2 S02｜5–10秒｜人物与商品互动

目标：

```text
手部
+
商品交互
+
动作自然度
+
商品一致性
```

建议第一版动作：

```text
伸手
→
拿起商品
→
简单使用
```

限制：

- 一个5秒镜头只安排一个主要动作组；
- 不同时安排复杂开盖、倒液体、转身、展示、微笑、指向镜头等动作；
- 如果“拿起+喝水”失败率高，V1.1直接降级为“伸手+拿起”。

---

## 6.3 S03｜10–15秒｜产品记忆点

目标：

```text
商品清晰
+
结束稳定
+
自然产品记忆
```

结构：

```text
使用结束
→
商品自然放回
→
人物回到正常状态
→
镜头轻微靠近商品或保持稳定
```

注意：

CTA、价格、优惠、产品卖点文字建议后期叠加，不让视频模型直接生成。

---

## 6.4 模板JSON

```json
{
  "template_id": "TPL_SCENE_PRODUCT_15S_V1",
  "video_type": "scene_experience",
  "duration_total": 15,
  "generation_strategy": "3x5s",
  "aspect_ratio": "9:16",
  "shots": [
    {
      "shot_id": "S01",
      "duration": 5,
      "purpose": "scene_establish",
      "product_interaction": "none",
      "difficulty": "low"
    },
    {
      "shot_id": "S02",
      "duration": 5,
      "purpose": "product_interaction",
      "product_interaction": "simple",
      "difficulty": "medium"
    },
    {
      "shot_id": "S03",
      "duration": 5,
      "purpose": "product_memory",
      "product_interaction": "low",
      "difficulty": "low"
    }
  ]
}
```

---

# 7. 首批20条 Prompt 模块

这些不是20条完整视频Prompt，而是Prompt Builder调用的基础积木。

---

## A. 人物真实感

### REAL_PERSON_001｜真实人物肤质

```text
人物具有真实自然的皮肤纹理，可见轻微毛孔、自然肤色变化和少量真实面部纹理，不过度磨皮，不过度美颜，不呈现蜡像、塑料皮肤或CG人物质感。
```

### REAL_PERSON_002｜自然微动作

```text
人物保持真实生活状态，自然眨眼、轻微呼吸，肩膀和头部存在非常细微的自然移动，动作不完全机械重复，不持续保持固定表情。
```

### REAL_PERSON_003｜非网红化

```text
人物外貌自然、生活化，不使用高度标准化网红脸，不夸张精致，不呈现商业模特式僵硬姿态，整体像普通手机真实记录中的真实用户。
```

---

## B. 场景

### SCENE_REAL_001｜办公室

```text
普通真实办公室工位，电脑、键盘、鼠标、手机和少量文件自然存在，桌面允许存在轻微使用痕迹和不完全整齐的物品摆放，不像摄影棚搭景，不使用夸张科技装饰。
```

### SCENE_REAL_002｜家庭

```text
普通真实家庭生活环境，家具、生活用品和背景物件自然摆放，空间允许存在轻微生活痕迹，不过分整洁，不呈现样板房或广告摄影棚效果。
```

### SCENE_REAL_003｜厨房/使用区

```text
真实日常使用空间，台面存在合理生活用品和轻微使用痕迹，材质、光线和物体比例符合真实环境，避免过度高级、过度整洁和不自然商业布景。
```

---

## C. 人物动作

### ACTION_001｜自然工作

```text
人物继续正常进行当前活动，不主动表演，不持续看镜头，动作幅度较小，节奏符合真实日常行为。
```

### ACTION_002｜自然伸手

```text
人物自然将一只手伸向附近商品，动作连续、缓慢、幅度适中，手臂和身体协调运动，不突然加速，不出现夸张动作。
```

### ACTION_003｜拿起商品

```text
人物自然握住商品并稳定拿起，手指位置符合真实抓握逻辑，商品重量感正常，商品不得变形、漂浮或突然改变比例。
```

### ACTION_004｜简单使用

```text
人物按照商品正常使用方式完成一个简单动作，只进行一个主要动作，不叠加复杂连续操作，动作自然、完整、符合真实人体运动逻辑。
```

### ACTION_005｜自然放回

```text
人物完成使用后自然将商品放回原有或合理位置，手部缓慢离开商品，商品稳定停留，不滑动、不漂浮、不突然消失。
```

---

## D. 镜头

### CAMERA_001｜手机轻手持

```text
使用真实手机拍摄般的轻微手持感，镜头存在非常轻微自然漂移，但整体稳定，不使用明显机械稳定器运动，不产生大幅摇晃。
```

### CAMERA_002｜固定中近景

```text
中近景固定构图，以人物上半身和商品所在区域为主要画面，镜头基本保持位置，只允许非常细微自然变化。
```

### CAMERA_003｜轻微慢推

```text
镜头以非常缓慢、克制的速度轻微靠近主体，移动距离较小，不快速推近，不突然改变焦段，不制造电影式夸张运镜。
```

---

## E. 光线

### LIGHT_001｜真实室内环境光

```text
使用普通室内真实环境光，亮度自然不过曝，人物与背景受光关系一致，不过度打亮面部，不使用明显棚拍轮廓光。
```

### LIGHT_002｜自然窗光

```text
柔和自然窗光进入室内，明暗关系符合真实时间和空间位置，允许存在轻微阴影和亮度不均，不制造完美商业广告光效。
```

---

## F. 商品一致性

### PRODUCT_LOCK_001｜商品身份锁定

```text
严格以输入商品参考图为唯一产品身份基准，保持商品外形、尺寸比例、包装结构、主色、瓶盖或盒体结构、Logo位置和主要视觉特征一致，不重新设计产品。
```

### PRODUCT_LOCK_002｜商品物理稳定

```text
商品在整个镜头中保持稳定物理形态，不拉伸、不缩小、不突然改变颜色、不增加或减少部件、不漂浮、不穿过手掌或其他物体。
```

---

## G. 负向规则

### NEGATIVE_001｜人物与动作

```text
禁止AI塑料脸、蜡像皮肤、过度磨皮、面部突然变化、年龄变化、人物身份漂移、多余手指、缺失手指、融合手指、手腕扭曲、肢体穿模、机器人动作、瞬移、异常身体比例。
```

### NEGATIVE_002｜商品与场景

```text
禁止商品变形、包装重新设计、Logo变化、商品颜色变化、商品数量突然改变、漂浮商品、物体融合、背景物体闪烁、场景结构变化、文字乱码、虚构包装文字、CG质感、游戏画面感。
```

---

# 8. Prompt Schema V1.0

原则：

```text
数据库保存结构化JSON
↓
Prompt Builder读取模块
↓
编译为模型自然语言Prompt
↓
模型适配层增加模型私有参数
↓
发送视频模型API
```

Schema：

```json
{
  "schema_version": "1.0",

  "task": {
    "task_id": "",
    "product_id": "",
    "video_type": "scene_experience",
    "duration_total": 15,
    "generation_strategy": "3x5s",
    "aspect_ratio": "9:16",
    "language": "zh-CN"
  },

  "source_assets": {
    "product_images": [],
    "character_images": [],
    "reference_video": null,
    "documents": []
  },

  "product": {
    "name": "",
    "brand": "",
    "category": "",
    "specification": "",
    "appearance_description": "",
    "confirmed_selling_points": [],
    "possible_selling_points": [],
    "usage_scenes": [],
    "forbidden_claims": [],
    "identity_lock": true
  },

  "evidence": {
    "source_level": "L1",
    "information_confidence": 0.0,
    "verified_facts": [],
    "unverified_facts": [],
    "allow_unverified_claims": false
  },

  "video_strategy": {
    "template_id": "TPL_SCENE_PRODUCT_15S_V1",
    "target_audience": "",
    "primary_selling_point": "",
    "creative_direction": "real_life",
    "complexity": "low"
  },

  "character": {
    "required": true,
    "identity_reference": null,
    "gender": "",
    "age_range": "",
    "appearance_type": "ordinary_real_person",
    "clothing": "",
    "expression": "natural",
    "eye_contact": "occasional",
    "realism_modules": [
      "REAL_PERSON_001",
      "REAL_PERSON_002",
      "REAL_PERSON_003"
    ]
  },

  "scene": {
    "scene_type": "",
    "location": "",
    "environment_objects": [],
    "realism_level": "high",
    "scene_module": ""
  },

  "lighting": {
    "lighting_type": "",
    "lighting_module": "",
    "brightness": "natural",
    "commercial_lighting": false
  },

  "shots": [
    {
      "shot_id": "S01",
      "duration": 5,
      "shot_size": "medium_close_up",
      "camera_module": "",
      "action_modules": [],
      "product_position": "",
      "product_interaction": "none",
      "start_frame_requirement": "",
      "end_frame_requirement": ""
    }
  ],

  "product_control": {
    "reference_required": true,
    "lock_modules": [
      "PRODUCT_LOCK_001",
      "PRODUCT_LOCK_002"
    ],
    "allow_package_redesign": false,
    "allow_logo_change": false,
    "allow_product_count_change": false
  },

  "negative_control": {
    "modules": [
      "NEGATIVE_001",
      "NEGATIVE_002"
    ]
  },

  "text_policy": {
    "allow_model_generated_text": false,
    "overlay_text_in_post": true
  },

  "audio_policy": {
    "generate_voice_in_video_model": false,
    "voiceover_post_process": true,
    "background_music": false
  },

  "model": {
    "provider": "",
    "model_name": "",
    "generation_mode": "image_to_video",
    "model_specific_parameters": {}
  },

  "prompt_output": {
    "compiled_positive_prompt": "",
    "compiled_negative_prompt": ""
  },

  "version": {
    "prompt_version": "1.0",
    "asset_status": "testing"
  }
}
```

---

# 9. Prompt自动组装顺序

Prompt Builder统一按照以下顺序组装：

```text
1. 镜头目标
↓
2. 人物
↓
3. 商品
↓
4. 场景
↓
5. 人物动作
↓
6. 商品交互
↓
7. 镜头
↓
8. 光线
↓
9. 人物真实感
↓
10. 商品锁定
↓
11. 负向约束
```

禁止每次让大模型完全自由发挥整条Prompt。

---

# 10. 三镜头基准 Prompt

这三条作为人工Baseline，用于对比系统自动组装Prompt质量。

---

## S01_V1.0｜真实场景建立

```text
第一帧直接显示一名30到40岁普通东亚女性坐在真实办公室工位正常工作，人物与输入参考商品同时已经存在于画面中。

女性外貌自然生活化，不是标准网红脸，不是商业模特脸。真实皮肤纹理，可见轻微毛孔、自然肤色差异和少量真实面部纹理，不过度磨皮，不过度美颜。

人物穿普通简洁办公室日常服装，姿态自然放松。

桌面存在电脑、键盘、鼠标、手机和少量文件，摆放自然，允许存在轻微使用痕迹和不完全整齐感，整体像真实正在使用的办公室，而不是摄影棚。

商品自然放在人物右手边桌面。

严格以输入商品参考图作为唯一商品身份基准，保持产品外形、比例、主色、包装结构、Logo位置和主要视觉特征一致，不重新设计商品。

0到5秒人物继续正常看电脑工作，右手轻微操作鼠标，自然眨眼，存在轻微呼吸和细小身体移动，不主动看镜头，不触碰商品。

采用中近景基本固定构图，只允许极轻微真实手机拍摄漂移。

使用普通办公室真实环境光，亮度自然，不使用商业摄影棚布光，不使用电影级轮廓光。

商品整个镜头保持稳定，不变形、不移动、不改变颜色、不改变数量。

禁止AI塑料脸、蜡像皮肤、面部突变、人物身份漂移、手指畸形、异常身体比例、商品变形、商品颜色变化、包装重新设计、Logo变化、商品数量变化、背景结构突变、物体闪烁、CG质感、游戏画面感、过度电影感。
```

---

## S02_V1.0｜自然拿起并简单使用

```text
第一帧保持与上一镜一致的真实办公室、同一女性、同一个商品和相同桌面空间关系。

女性坐在办公桌前，人物身份、脸型、年龄感、发型、服装和肤色保持稳定。

商品开始时自然放在人物右手边桌面。

严格以输入商品参考图作为唯一产品身份基准，商品在整个镜头中必须保持原有外形、尺寸比例、主色、包装结构、Logo位置和主要视觉特征一致。

0到1秒，人物继续正常看电脑。

约1秒后，人物自然将右手伸向桌面上的商品。动作缓慢、连续、幅度较小，肩膀、手臂和身体协调运动。

人物使用真实自然的单手抓握方式握住商品，手指与商品位置符合真实人体抓握逻辑。

人物稳定拿起商品，不快速移动，不旋转展示商品。

随后只完成一个简单正常使用动作。

整段不增加其他表演动作，不挥手，不展示，不指向镜头，不夸张微笑。

人物不持续看镜头。

采用中近景固定构图，不推镜、不环绕、不快速移动镜头。

普通办公室环境光，人物皮肤真实，保留轻微毛孔和自然肤质。

商品必须具有正常重量感，不漂浮、不穿过手掌、不拉伸、不变形、不突然改变大小。

禁止多余手指、缺失手指、融合手指、手腕扭曲、手穿商品、肢体穿模、机器人动作、突然瞬移、商品变形、商品身份改变、Logo改变、包装重绘、商品数量改变、面部突变、AI塑料皮肤、背景闪烁、镜头突然移动。
```

---

## S03_V1.0｜放回商品并形成产品记忆

```text
第一帧保持同一空间、同一女性和同一个商品。

女性刚完成简单使用动作，右手自然持有商品。

严格保持商品与输入参考图身份一致，外形、包装、颜色、尺寸比例和主要视觉特征不得发生变化。

0到2秒，人物自然将商品缓慢放下。

2到3.5秒，人物把商品稳定放回右侧桌面靠前位置。

手部自然离开商品。

商品放置后保持稳定，不滑动、不漂浮、不旋转、不改变外观。

3.5到5秒，人物自然将注意力重新回到原本活动。

镜头仅进行非常轻微、缓慢的靠近，使桌面商品在结束画面中更加清晰，但不进行夸张产品特写。

人物动作自然，表情普通，不对镜头进行商业展示。

桌面保持真实使用痕迹。

使用普通环境光。

最终画面需要同时保留人物生活状态和清晰商品主体，像真实短视频记录，而不是摄影棚产品广告。

禁止人物身份改变、商品变形、商品颜色变化、Logo变化、包装重新设计、多余手指、手部穿模、商品漂浮、商品突然位移、背景家具变化、背景闪烁、快速推镜、过度景深、过度广告质感、CG画面。
```

---

# 11. 视频模型适配层要求

业务逻辑不要直接写死某个视频模型。

建议统一接口：

```python
generate_video(
    provider="",
    model="",
    prompt="",
    image_url="",
    duration=5,
    aspect_ratio="9:16"
)
```

统一返回：

```json
{
  "provider": "",
  "model": "",
  "provider_task_id": "",
  "internal_task_id": "",
  "status": "submitted"
}
```

后续可扩展：

```text
Kling
Seedance
Veo
WAN
其他模型
```

---

# 12. 任务状态

统一状态建议：

```text
CREATED
SUBMITTED
PROCESSING
COMPLETED
FAILED
QA_PENDING
PASS
REPAIR
REJECTED
```

正常流程：

```text
CREATED
↓
SUBMITTED
↓
PROCESSING
↓
COMPLETED
↓
QA_PENDING
↓
PASS / REPAIR / REJECTED
```

---

# 13. 每次生成必须保存的数据

每次生成都必须记录：

```json
{
  "internal_task_id": "",
  "product_id": "",
  "shot_id": "",
  "provider": "",
  "model": "",
  "prompt_version": "",
  "prompt_text": "",
  "duration": 5,
  "video_url": "",
  "created_at": "",
  "generation_status": "",
  "generation_time_seconds": null,
  "estimated_cost": null,
  "qa_score": null,
  "qa_status": null,
  "failure_codes": []
}
```

禁止只保存最终成片。

---

# 14. QA质量评分 V1.0

总分：

```text
100
```

| QA项目 | 权重 | 核心检查 |
|---|---:|---|
| 商品一致性 | 20 | 外形、颜色、包装、Logo是否稳定 |
| 人物真实性 | 15 | AI脸、肤质、人物身份 |
| 动作自然度 | 15 | 动作连续性、速度、真实感 |
| 手部与肢体 | 10 | 手指、穿模、身体比例 |
| Prompt遵循度 | 10 | 是否完成要求动作 |
| 场景真实性 | 10 | 是否像真实生活环境 |
| 镜头合理性 | 5 | 运镜是否过大、构图是否正确 |
| 画面稳定性 | 5 | 闪烁、跳变、背景突变 |
| 商品信息准确性 | 5 | 是否出现虚构或错误信息 |
| 合规性 | 5 | 是否出现高风险宣传 |

---

## 14.1 QA结果

```text
QA >= 85
PASS

70 <= QA < 85
REPAIR

QA < 70
FAIL
```

---

## 14.2 强制失败 HARD FAIL

以下情况不看总分，直接FAIL：

```text
HARD_FAIL_01
商品明显变成其他商品

HARD_FAIL_02
Logo明显错误

HARD_FAIL_03
商品严重变形

HARD_FAIL_04
人物出现严重肢体畸形

HARD_FAIL_05
出现未经资料证明的医疗、功效、收益事实

HARD_FAIL_06
生成错误价格、规格等关键商品信息

HARD_FAIL_07
出现无法接受的严重文字乱码
```

---

# 15. Failure Code V1.0

Failure Code必须用于定位问题，禁止只记录“效果不好”。

---

## PERSON｜人物

### PER001｜AI_FACE

表现：

```text
明显AI脸、塑料脸、蜡像感
```

建议Repair：

```text
strengthen_real_person_prompt
```

---

### PER002｜IDENTITY_DRIFT

表现：

```text
人物前后不像同一个人
```

建议Repair：

```text
strengthen_character_identity
reduce_head_rotation
reduce_action_complexity
```

---

### PER003｜SKIN_OVER_SMOOTH

表现：

```text
皮肤过度磨皮、没有真实纹理
```

建议Repair：

```text
strengthen_skin_texture
remove_beauty_keywords
```

---

## HAND｜手与身体

### HAND001｜FINGER_DEFORMATION

表现：

```text
多指、少指、粘连、融合
```

建议Repair：

```text
reduce_action_complexity
use_single_hand
```

---

### HAND002｜BODY_DEFORMATION

表现：

```text
手臂、肩膀、身体结构异常
```

建议Repair：

```text
reduce_motion_range
regenerate_shot
```

---

### HAND003｜OBJECT_PENETRATION

表现：

```text
手穿过商品或商品穿模
```

建议Repair：

```text
simplify_grip
reduce_relative_motion
```

---

## PRODUCT｜商品

### PRO001｜PRODUCT_DEFORMATION

表现：

```text
商品拉伸、缩小、结构变化
```

建议Repair：

```text
strengthen_product_lock
```

---

### PRO002｜PRODUCT_IDENTITY_CHANGE

表现：

```text
商品变成另一款商品
```

建议Repair：

```text
hard_fail
regenerate_with_reference
```

---

### PRO003｜LOGO_ERROR

表现：

```text
Logo变化、Logo位置错误、品牌字错误
```

建议Repair：

```text
avoid_logo_closeup
use_reference_image
post_overlay_if_needed
```

---

### PRO004｜PACKAGE_TEXT_ERROR

表现：

```text
包装文字乱码或被重绘
```

建议Repair：

```text
disable_model_generated_text
use_original_product_reference
post_overlay_text
```

---

### PRO005｜PRODUCT_COUNT_CHANGE

表现：

```text
一件产品突然变成多件
```

建议Repair：

```text
enforce_single_product
```

---

## MOTION｜动作

### MOT001｜ROBOTIC_MOTION

表现：

```text
动作僵硬、机器人感
```

建议Repair：

```text
strengthen_natural_micro_motion
reduce_motion_complexity
```

---

### MOT002｜ACTION_TOO_FAST

表现：

```text
动作节奏过快
```

建议Repair：

```text
reduce_action_count
slow_action
```

---

### MOT003｜ACTION_JUMP

表现：

```text
突然跳帧、瞬移、动作不连续
```

建议Repair：

```text
regenerate_shot
```

---

### MOT004｜ACTION_NOT_FOLLOWED

表现：

```text
模型没有执行指定动作
```

建议Repair：

```text
move_primary_action_earlier
remove_competing_instructions
```

---

## CAMERA｜镜头

### CAM001｜CAMERA_OVERMOVE

表现：

```text
推拉摇移过大、乱运镜
```

建议Repair：

```text
switch_fixed_camera
```

---

### CAM002｜CAMERA_SHAKE

表现：

```text
抖动过大
```

建议Repair：

```text
reduce_handheld_strength
```

---

### CAM003｜FRAMING_ERROR

表现：

```text
人物或商品被裁切
```

建议Repair：

```text
rebuild_first_frame_composition
```

---

## SCENE｜场景

### SCN001｜SCENE_AI_LOOK

表现：

```text
环境太完美、太假、棚拍感严重
```

建议Repair：

```text
add_life_traces
remove_cinematic_luxury_keywords
```

---

### SCN002｜BACKGROUND_MUTATION

表现：

```text
背景家具结构不断变化
```

建议Repair：

```text
simplify_background
reduce_camera_motion
```

---

### SCN003｜OBJECT_FLICKER

表现：

```text
背景物品闪烁、消失
```

建议Repair：

```text
reduce_background_objects
regenerate_shot
```

---

## TEXT｜文字

### TXT001｜TEXT_GARBLED

表现：

```text
乱码、错误文字
```

建议Repair：

```text
disable_model_generated_text
post_overlay_text
```

---

### TXT002｜PRODUCT_INFO_WRONG

表现：

```text
规格、价格、参数等错误
```

建议Repair：

```text
hard_fail
use_structured_verified_data
```

---

## COMPLIANCE｜合规

### CMP001｜UNVERIFIED_CLAIM

表现：

```text
AI自己添加资料中不存在的卖点
```

建议Repair：

```text
remove_unverified_claim
regenerate_script
```

---

### CMP002｜MEDICAL_CLAIM

表现：

```text
治疗、治愈、疾病相关功效表达
```

建议Repair：

```text
hard_block
```

---

### CMP003｜ABSOLUTE_CLAIM

表现：

```text
第一、最好、100%、永久等绝对化表达
```

建议Repair：

```text
compliance_rewrite
```

---

# 16. Failure Code → Repair流程

技术逻辑建议：

```text
视频生成完成
↓
QA
↓
检测Failure Code
↓
根据Failure Code匹配repair_action
↓
生成Prompt新版本
↓
只重跑对应shot_id
↓
重新QA
```

示例：

```text
HAND001
↓
reduce_action_complexity
↓
S02_V1.1
↓
只重跑S02
```

另一个示例：

```text
PRO001
↓
strengthen_product_lock
↓
S02_V1.1
↓
只重跑S02
```

---

# 17. Prompt版本规则

任何Prompt修改不得覆盖旧版本。

示例：

```text
S02_V1.0
首次测试

S02_V1.1
降低动作复杂度

S02_V1.2
加强商品锁定

S02_V1.3
切固定镜头
```

至少需要能够统计：

```text
Prompt版本
+
模型
+
生成次数
+
PASS次数
+
FAIL次数
+
平均QA
+
单条平均成本
+
平均耗时
```

---

# 18. 明日第一轮测试方法

## Round 1

生成：

```text
S01 × 3
S02 × 3
S03 × 3
```

合计：

```text
9条5秒测试视频
```

每条必须记录：

```text
Test_ID
Product_ID
Shot_ID
Model
Prompt_Version
Generation_Time
Cost
QA_Score
Status
Failure_Code
Problem
Repair_Action
Next_Version
```

---

# 19. 优化测试

如果结果例如：

```text
S01：3次PASS
S02：1次PASS / 1次REPAIR / 1次FAIL
S03：2次PASS / 1次REPAIR
```

优先只优化：

```text
S02
```

如果S02失败原因：

```text
HAND001
PRO001
MOT002
```

V1.1调整：

```text
原动作：
伸手 → 拿起 → 使用

调整为：
伸手 → 拿起
```

并：

```text
加强 PRODUCT_LOCK_001
加强 PRODUCT_LOCK_002
固定镜头
```

然后：

```text
S02_V1.1 × 3
```

对比V1.0和V1.1通过率。

---

# 20. 第二模型A/B测试

第一模型闭环跑通后，再测试第二模型。

要求保持：

```text
同一个商品
同一个商品图
同一个镜头结构
同一个Prompt版本
同一个时长
同一个比例
```

只修改：

```text
provider
model_name
```

比较：

| 指标 | 模型A | 模型B |
|---|---:|---:|
| 商品一致性 |  |  |
| 人物真实性 |  |  |
| 手部成功率 |  |  |
| 动作自然度 |  |  |
| Prompt遵循度 |  |  |
| 首帧保持 |  |  |
| 生成耗时 |  |  |
| 单条成本 |  |  |
| 平均QA |  |  |
| 首次PASS率 |  |  |

---

# 21. AIGC内容岗明日职责

AIGC内容岗负责：

1. 选定1个测试商品；
2. 提供1～3张清晰商品图；
3. 确认测试视频类型；
4. 提供15秒模板；
5. 提供20条Prompt模块；
6. 提供Prompt Schema；
7. 提供S01/S02/S03人工Baseline Prompt；
8. 明确禁止项；
9. 对每条生成视频做QA；
10. 给失败视频打Failure Code；
11. 修改失败Prompt形成V1.1；
12. 对比V1.0与V1.1；
13. 判断哪些动作、镜头、Prompt模块可进入Validated。

AIGC内容岗不负责：

```text
FastAPI底层实现
API鉴权
SDK封装
Redis
任务队列
数据库连接
OSS上传
Webhook
并发调度
轮询实现
```

---

# 22. 技术/工作流岗位明日职责

技术负责：

1. 建立最简测试入口；
2. 接收商品名称与商品图；
3. 生成/接收结构化商品JSON；
4. 读取template_id；
5. 读取Prompt Module；
6. 实现Prompt Builder；
7. 接入一个视频模型API；
8. 实现提交任务；
9. 保存provider_task_id；
10. 实现任务状态查询；
11. 获取视频结果；
12. 保存生成结果；
13. 支持QA结果回写；
14. 支持Failure Code保存；
15. 支持单镜头重跑；
16. 支持Prompt版本；
17. 将S01/S02/S03拼接成15秒测试视频；
18. 输出生成耗时、失败率、成本等测试统计。

---

# 23. 推荐接口形式

第一版不要求正式前端。

FastAPI、Python脚本、Postman均可。

可以设计类似：

```text
POST /products/analyze

POST /video-plan/generate

POST /prompts/compile

POST /video/generate

GET /video/tasks/{task_id}

POST /video/tasks/{task_id}/qa

POST /video/tasks/{task_id}/retry
```

注意：

接口名可以调整，核心是职责分离。

---

# 24. 推荐统一生成接口

```json
{
  "product_id": "TEST_001",
  "shot_id": "S02",
  "provider": "kling",
  "model": "MODEL_NAME",
  "prompt_version": "1.0",
  "prompt": "最终编译Prompt",
  "image_url": "PRODUCT_OR_FIRST_FRAME_URL",
  "duration": 5,
  "aspect_ratio": "9:16"
}
```

返回：

```json
{
  "internal_task_id": "TASK_0001",
  "provider_task_id": "xxxx",
  "status": "SUBMITTED"
}
```

---

# 25. QA回写示例

```json
{
  "internal_task_id": "TASK_0001",
  "shot_id": "S02",
  "qa_score": 66,
  "qa_status": "FAIL",
  "failure_codes": [
    "HAND001",
    "PRO001"
  ],
  "failure_notes": [
    "手指融合",
    "商品拿起后明显变形"
  ],
  "repair_actions": [
    "reduce_action_complexity",
    "strengthen_product_lock"
  ],
  "next_prompt_version": "1.1"
}
```

---

# 26. 最终数据最低要求

第一轮至少保存：

```text
task_id
product_id
shot_id
template_id
provider
model
prompt_version
prompt_text
source_image
video_url
generation_time
cost
qa_score
qa_status
failure_codes
repair_action
created_at
```

---

# 27. 明日最终交付物

## AIGC内容侧

明天下班前至少得到：

```text
1套经过真实测试的15秒模板
3条真实测试Prompt
9～12条测试视频
1份QA记录
1份Failure Code统计
至少1条V1.1 Prompt
动作稳定性结论
商品一致性结论
模型执行能力结论
```

## 技术侧

明天下班前至少得到：

```text
一个可调用测试入口
一个视频模型API接通
task_id流程跑通
视频结果正常获取
任务数据可保存
QA可回写
Failure Code可保存
单镜头可重跑
Prompt版本可追踪
3个镜头可拼15秒
```

---

# 28. 明日验收标准

只看以下8项：

```text
① 能输入商品
② 能自动生成/读取结构化商品档案
③ 能自动组装Prompt
④ 能调用视频API并返回结果
⑤ 能记录QA
⑥ 能明确记录Failure Code
⑦ 能针对失败镜头生成V1.1并单独重跑
⑧ 第二轮质量或首次PASS率比第一轮提高
```

如果这8项成立：

```text
AI带货视频自动生产PoC第一阶段验证成功
```

---

# 29. 本轮暂不判断的内容

本轮不以以下指标作为失败条件：

- 尚未支持日产1000条；
- 尚未建立向量数据库；
- 尚未支持全部商品类目；
- 尚未支持全部视频类型；
- 尚未完成自动剪辑；
- 尚未完成口播；
- 尚未完成字幕；
- 尚未完成投流；
- 尚未完成多模型自动路由。

这些均属于PoC之后的工程化阶段。

---

# 30. 后续路线

PoC跑通后，再依次进入：

```text
阶段1
1商品 × 1视频类型 × 1模型
↓

阶段2
3商品 × 同品类
验证Prompt复用
↓

阶段3
3个主流品类
食品 / 美妆 / 家居
↓

阶段4
扩展40～60套视频模板
↓

阶段5
扩展200～300个Prompt模块
↓

阶段6
多模型自动路由
↓

阶段7
QA半自动/自动化
↓

阶段8
批量并发与成本优化
↓

阶段9
日产百条/千条压力测试
```

---

# 31. 当前最重要原则

### 原则1

```text
用户输入简单
≠
内部没有结构
```

应该是：

```text
用户输入简单
+
系统内部自动结构化
```

### 原则2

```text
Prompt数量多
≠
生成质量高
```

真正应该积累的是：

```text
已验证Prompt
+
成功率
+
Failure Code
+
适用模型
+
适用场景
+
版本记录
```

### 原则3

```text
不要把视频模型当成万能执行器
```

视频模型负责：

```text
人物
场景
动作
商品交互
镜头
```

后期系统负责：

```text
字幕
CTA
价格
Logo覆盖
复杂准确文字
音乐
配音
成片合成
```

### 原则4

第一阶段成功标准不是：

```text
生成一条特别漂亮的视频
```

而是：

```text
能够稳定生成
能够判断失败
能够知道为什么失败
能够修改
能够再次生成
能够提高成功率
```

---

# 32. 文档版本

```text
Document Version: V1.0
Prompt Schema: V1.0
Template: TPL_SCENE_PRODUCT_15S_V1
Prompt Asset Status: TESTING
```

测试完成后根据真实结果更新：

```text
V1.0
↓
V1.1
↓
Validated
↓
Production
```
