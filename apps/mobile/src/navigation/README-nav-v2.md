# 导航 v2 提案（未接入 App.tsx）

这一目录是「底部三个 tab + 记录聚合 + 我的」的**提案实现**，用来先看代码结构、再决定要不要并进主流程。**App.tsx 没有改动**，现有界面与测试不受影响。

## 三屏与现有页面的关系

| 新页面 | 文件 | 复用什么 | 需要新写什么 |
|---|---|---|---|
| 对话（保留） | `src/chat/ChatScreen.tsx` | 原样保留 | 只把首页大按钮换成输入框上方的 chips（未做） |
| 记录 | `src/navigation/record-screen.tsx` | `api.getGlucoseReadings` / `getGlucoseSummary` | 血糖（手动 / 传感器两个来源）、饮食 / 用药 / 运动 / 睡眠四个分栏 |
| 我的 | `src/navigation/profile-screen.tsx` | 健康档案、健康看板、沟通方式、医护授权、设置等既有页面 | 只做入口列表，不改这些页面本身 |
| 底部导航 | `src/navigation/tab-bar.tsx` | — | 三个 tab；替换原来的抽屉菜单 |

## 接进 App.tsx 需要的改动（讨论后再做）

1. `App.tsx` 里患者分支从直接渲染 `ChatScreen` 改为渲染一个 `AppShell`：顶部内容按 `activeTab` 切换、底部固定 `AppTabBar`。
2. `ChatScreen` 需要能接收「当前可见」标志：不可见时暂停自动滚动与提示音，否则切换 tab 会把已有对话的滚动位置弄乱。
3. `app-menu.tsx` 里的抽屉条目要么删除，要么只保留「历史对话」（但现在 `profile-screen` 里已经有入口）。
4. 记录页的写操作（记一条血糖、导入传感器、记一餐）复用既有 Modal，需要把这些 Modal 的状态从 `ChatScreen` 提到 `AppShell`，否则切 tab 会丢。
5. 医护端（`ClinicianScreen`）不受影响。

## 待组里讨论的三点

1. **是否保留抽屉**：抽屉的条目与底部 tab 完全重叠。倾向删除，但「历史对话」在抽屉里更好找（现在放进了「我的」）。
2. **记录页的分栏**：按论文的 glucose / meals / medication / physical activity / sleep 五栏；stress 与 daily routines 目前落在「我的 › 沟通方式」与主动关怀里，没有单独页签。要不要为 stress 单独开一栏？
3. **血糖与传感器的关系**：现在是「血糖」分栏内的两个来源（手动 / 传感器），不是一个栏目一个数据源。这样避免患者不理解「我手输的数字该去哪个页面」，但需要在预览页与文案上说清。

## 测试

- `tab-bar.test.tsx`：选中态与切换回调
- `record-screen.test.tsx`：手输读数的展示、切到传感器后的导入入口、饮食分栏的「下周」占位、单位换算与时间格式
- `profile-screen.test.tsx`：健康档案 / 健康看板仍可达、点击回调

这些测试只覆盖新页面，不改动既有测试。
