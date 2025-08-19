# QwenImage 插件

QwenImage是一个适用于dow-859-ipad项目的画图插件，调用阿里云官方API进行文生图和图生图的插件，支持多种模型和参数配置。

> dow-859-ipad项目链接: https://github.com/Lingyuzhou111/dow-ipad-859

> 获取API密钥: 访问 [阿里云通义千问](https://dashscope.console.aliyun.com/) 获取API密钥

## 主要特性

### 1. 多模型支持
- **qwen-image**: 默认模型，平衡质量和速度
- **qwen-image-edit**: 20250818最新发布的图像编辑模型
- **wan2.2-t2i-flash**: 快速生成模型，适合快速预览
- **wan2.2-t2i-plus**: 高质量模型，适合最终作品

### 2. 智能模型选择
- 默认使用 `qwen-image` 模型
- 使用 `--flash` 参数调用 `wan2.2-t2i-flash` 模型
- 使用 `--plus` 参数调用 `wan2.2-t2i-plus` 模型

### 3. 灵活的图片尺寸
支持多种预设比例：
- 1:1 (1328x1328) - 默认比例
- 3:4 (1140x1472)
- 4:3 (1472x1140)
- 16:9 (1664x928)
- 9:16 (928x1664)

### 4. 智能扩写功能
- 可开启/禁用智能扩写功能
- 对短提示词效果提升明显
- 支持用户个性化设置

### 5. 多账号支持
- 支持配置两个API密钥
- 可动态切换账号
- 自动负载均衡

## 使用方法

### 基本绘图命令
```
Q画图 [描述] [参数]
Q生成 [描述] [参数]
```

### 参数说明

#### 图片尺寸
```
--ar 16:9    # 设置图片比例为16:9
--ar 3:4     # 设置图片比例为3:4
```

#### 模型选择
```
--flash      # 使用快速生成模型
--plus       # 使用高质量模型
# 不指定参数时使用默认的qwen-image模型
```

#### 负面提示词
```
--负面提示：模糊，低质量，过曝
```

### 使用示例

#### 基础绘图
```
Q画图 一只可爱的小猫
Q生成 美丽的风景画
```

#### 指定尺寸和模型
```
Q画图 一张酷炫的电影海报 --ar 3:4 --plus
Q生成 快速生成的风景画 --ar 16:9 --flash
Q画图 美丽的花朵 --负面提示：模糊，低质量
```

### 控制命令

#### 智能扩写控制
```
Q开启智能扩写    # 开启智能扩写功能
Q禁用智能扩写    # 禁用智能扩写功能
```

#### 账号切换
```
Q切换账号 1      # 切换到账号1
Q切换账号 2      # 切换到账号2
```

## 配置说明

### 配置文件结构
```json
{
"image_command": ["Q画", "Q画图", "Q生成"],
"image_edit_command": ["Q改图", "Q编辑"],
"control_command": ["Q开启智能扩写", "Q禁用智能扩写"],
"account_command": ["Q切换账号 1", "Q切换账号 2"],
"api_key_1": "your_api_key_1",
"api_key_2": "your_api_key_2",
"qwen_image_edit": {
    "base_url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    "model": ["qwen-image-edit"]
    },
"qwen_image": {
    "base_url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
    "model": ["qwen-image", "wan2.2-t2i-flash", "wan2.2-t2i-plus"],
    "default_ratio": "1:1",     
    "default_negative_prompt": "色调艳丽，过曝，静态，细节模糊不清，风格，画面，整体发灰，最差质量，低质量， JPEG压缩残留，丑陋的，残缺的，多余的手指，杂乱的背景，三条腿",
    "ratios": {
        "1:1": {"width": 1328, "height": 1328},
        "3:4": {"width": 1140, "height": 1472},
        "4:3": {"width": 1472, "height": 1140},
        "16:9": {"width": 1664, "height": 928},
        "9:16": {"width": 928, "height": 1664}
        }
    }
}
```

### 配置项说明
- **image_command**: 绘图命令前缀列表
- **control_command**: 控制命令前缀列表
- **account_command**: 账号切换命令前缀列表
- **base_url**: DashScope API 基础URL
- **model**: 支持的模型列表
- **api_key_1/2**: 两个API密钥
- **default_ratio**: 默认图片比例
- **default_negative_prompt**: 默认负面提示词
- **ratios**: 图片尺寸配置

## 技术特性

### 异步处理
- 使用 DashScope 异步API
- 支持长时间任务轮询
- 自动重试机制

### 错误处理
- 完善的异常捕获
- 详细的日志记录
- 用户友好的错误提示

### 性能优化
- 智能提示词清理
- 参数解析优化
- 内存使用优化

## 注意事项

1. **API密钥**: 请确保配置有效的 DashScope API 密钥
2. **模型选择**: 根据需求选择合适的模型，flash模型速度快，plus模型质量高
3. **智能扩写**: 对短提示词效果提升明显，长提示词可能影响不大
4. **图片尺寸**: 不同比例适合不同的使用场景
5. **负面提示词**: 合理使用负面提示词可以提升图片质量

## 更新日志

### v1.0.0
- 初始版本发布
- 支持三种文生图模型
- 实现智能模型选择逻辑
- 添加多账号支持
- 完善参数解析和提示词清理
- 优化帮助文档和示例

## 技术支持

如有问题或建议，请查看日志文件或联系开发者。 

