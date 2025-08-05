# QwenImage 插件

QwenImage是一个适用于dow-859-ipad项目的画图插件，调用阿里云官方API进行文生图的插件，支持多种模型和参数配置。

> dow-859-ipad项目链接: https://github.com/Lingyuzhou111/dow-ipad-859

> 获取API密钥: 访问 [阿里云通义千问](https://dashscope.console.aliyun.com/) 获取API密钥

## 功能特性

- 🎨 **文生图功能**：支持多种"wan2.2-t2i-flash"和"wan2.2-t2i-plus"模型
- 📐 **多种尺寸比例**：支持1:1、2:3、3:4、4:3、3:2、16:9、9:16等比例
- 🚀 **智能扩写控制**：可随时开启或禁用API的智能扩写功能
- 🔄 **多账号切换**：支持配置多个API账号并随时切换
- ⚡ **异步处理**：使用异步API，支持长时间任务轮询

## 配置说明

### config.json 配置

```json
{
  "image_command": ["Q画图", "Q生成"],
  "control_command": ["Q开启智能扩写", "Q禁用智能扩写"],
  "account_command": ["Q切换账号 1", "Q切换账号 2"],
  "qwen_image": {
    "base_url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
    "model": ["wan2.2-t2i-flash", "wan2.2-t2i-plus"],
    "api_key_1": "your_api_key_1",
    "api_key_2": "your_api_key_2",
    "default_ratio": "1:1",
    "ratios": {
      "1:1": {"width": 1024, "height": 1024},
      "2:3": {"width": 896, "height": 1344},
      "3:4": {"width": 960, "height": 1280},
      "4:3": {"width": 1280, "height": 960},
      "3:2": {"width": 1344, "height": 896},
      "16:9": {"width": 1344, "height": 768},
      "9:16": {"width": 768, "height": 1344}
    }
  }
}
```

### 配置参数说明

- `image_command`: 绘图命令前缀
- `control_command`: 智能扩写控制命令
- `account_command`: 账号切换命令
- `qwen_image.model`: 可用的模型列表
- `qwen_image.api_key_1/2`: API密钥配置
- `qwen_image.ratios`: 支持的图片比例配置

## 使用方法

### 基础绘图命令

```
Q画图 一只可爱的小猫
Q生成 一张酷炫的电影海报
```

### 指定图片比例

```
Q画图 一只可爱的小猫 --ar 16:9
Q生成 一张电影海报 --ar 3:4
```

### 使用Plus模型

```
Q画图 一张酷炫的电影海报 --ar 3:4 --plus
```

### 智能扩写控制

```
Q开启智能扩写    # 开启智能扩写功能
Q禁用智能扩写    # 禁用智能扩写功能
```

### 账号切换

```
Q切换账号 1      # 切换到账号1
Q切换账号 2      # 切换到账号2
```

## 支持的模型

- `wan2.2-t2i-flash`: 快速生成模型（默认）
- `wan2.2-t2i-plus`: 高质量生成模型

## 功能特点

### 1. 智能扩写功能
- 默认开启智能扩写，对短提示词效果提升明显
- 支持用户级别的智能扩写设置
- 可随时开启或禁用，设置会保持到下次修改

### 2. 多账号支持
- 支持配置两个API账号
- 可随时切换账号，无需重启插件
- 自动检查账号配置有效性

### 3. 异步处理
- 使用DashScope异步API
- 支持长时间任务轮询
- 自动处理任务状态和错误

### 4. 用户状态管理
- 每个用户的智能扩写设置独立保存
- 基于session_id进行用户区分
- 设置持久化到插件运行期间

## 错误处理

插件包含完善的错误处理机制：

- API请求失败处理
- 任务轮询超时处理
- 账号配置验证
- 用户输入参数验证

## 日志记录

插件提供详细的日志记录：

- 用户操作日志
- API调用日志
- 错误信息日志
- 任务状态日志

## 注意事项

1. 确保API密钥配置正确
2. 智能扩写功能对短提示词效果更明显
3. 账号切换需要确保目标账号已配置API密钥
4. 长时间任务可能需要等待，请耐心等待结果

## 更新日志

### v1.0.0
- 初始版本发布
- 支持基础文生图功能
- 支持多种图片比例
- 支持智能扩写控制

- 支持多账号切换 

