# LEDI Light

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Home Assistant 自定义集成：本地 BLE 控制 LEDI AT5 80W 水族/植物灯。

## 功能

- ✅ 总电源开关
- ✅ 3 种预设光源（混合 / 青翠 / 胭红）
- ✅ 3 个自定义槽（保存自己的光谱）
- ✅ 5 路通道独立调节（RGBW + UV）
- ✅ 2 个独立定时器 + 日出/日落渐变
- ✅ 风扇温控（低/高档）
- ✅ 估算功率显示

## 安装

### 方式 1：HACS（推荐）

1. 打开 **HACS** → **集成**
2. 点击右上角 **⋮** → **自定义存储库**
3. 粘贴：`https://github.com/你的用户名/leedi_light`
4. Category 选 **Integration** → 点击 **添加**
5. 在 HACS 里搜索 **LEDI** → **Download**
6. **重启 Home Assistant**

### 方式 2：手动

1. 下载本仓库
2. 把 `custom_components/leedi_light` 复制到 HA 的 `/config/custom_components/`
3. **重启 Home Assistant**

## 配置

1. **设置 → 设备与服务 → + 添加集成**
2. 搜索 **LEDI** → 选择你的灯（MAC 结尾如 `AA:61`）
3. 点击集成卡片 → **配置** 按钮：
   - 配置 3 个预设的功率
   - 配置 2 个定时器
   - 配置风扇参数

## 使用

### 预设模式
- 选择 **混合 / 青翠 / 胭红**
- 拖 **功率** 滑块调整整体亮度

### 自定义模式
- 选择 **自定义一/二/三**
- 拖 5 个通道滑块调整颜色
- 点 **保存到自定义槽** 保存

### 定时器
- 在 **配置** 里设置时间
- 设备固件自主执行，HA 关机也有效

## 兼容性

- Home Assistant ≥ 2024.1.0
- 支持设备：LEDI AT5 80W（`X-G-*` 广播名）

## 已知问题

- UV 通道设备不上报状态，HA 显示的是本地设定值
- 灯体温度需要设备支持 NTC 传感器

## 许可

MIT License