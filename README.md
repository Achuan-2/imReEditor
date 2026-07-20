# imgReEditor

一个基于 PySide6 的 Windows 桌面图片编辑器，可打包为 exe 并制作成安装程序。
特色：保存的 PNG 可以再次打开并继续编辑所有标注。

## 功能

- 基础编辑：水平/垂直翻转、左旋/右旋 90°
- **可逆裁剪**：裁剪模式显示完整原图（选区外变暗），8 手柄调整/移动/重绘选区，
  ✓ 应用 ✗ 取消（Enter/Esc 同效）；再次点击「裁剪」随时恢复原图重选；
  原图与选区随 PNG 元数据保存，重新打开仍可重裁；选区拉回整图即移除裁剪
- 图片导入：打开 / 新建 / **直接拖入图片** / **Ctrl+V 粘贴**（截图、浏览器图片、
  图片文件均可；导入之前保存的 PNG 会自动恢复可编辑标注）
- 标注工具：矩形、圆形、直线、箭头、序号（自动递增）、文字、画笔、橡皮擦、马赛克
- **右侧工具设置栏**（仿 PixPin）：切换工具显示对应参数页——矩形/圆形的描边+填充+
  填充透明度，文字的字体/字号/粗体/斜体/描边/背景，序号大小，橡皮擦大小，马赛克块大小；
  选择模式下单选标注时侧栏自动切换为该标注的参数页并实时编辑
- **文字直接在画布中输入**：文字工具单击即出现输入框（Enter 完成、Shift+Enter 换行、
  Esc 取消、点击他处自动完成），选择模式双击已有文字就地修改，无弹窗
- **边框装裱**：纯色/渐变预设背景、边框间距、图片圆角、边框圆角、阴影大小/颜色/透明度；
  **直接实时显示在画布里**（非模态面板，调参即所见），参数随元数据保存，可随时调整或移除
- 选择模式：拖动移动标注、**选框手柄调整大小**（单选显示 8 手柄选框，直线/箭头/画笔按
  包围盒缩放，文字调字号，序号调半径）、`Delete` 删除选中、双击文字重新编辑、
  改色/改线宽作用于选中标注
- **即选即调**：绘制工具下点击同类型已有标注即可选中（显示选框、侧栏联动调参），
  拖本体移动、拖手柄缩放、点空白恢复新增，无需来回切换选择工具
- 界面：全部按钮使用 SVG 图标（随主题自动变色），qdarktheme 主题，
  可在「跟随系统/亮色/暗色」间切换并记住选择
- 滚轮缩放画布
- **二次编辑**：保存的 PNG 内嵌了「干净底图 + 全部标注的矢量数据 + 边框设置」，
  用本编辑器重新打开后，所有标注都能继续移动、修改、删除、重新标注

## 二次编辑原理

保存时写入两部分内容：

1. PNG 像素本身 = 底图 + 所有标注合成后的最终效果（任何看图软件都能正常查看）
2. PNG 的 `zTXt` 元数据块（key 为 `ImageEditorData`）= JSON，内含
   - `base`：不含标注的干净底图（PNG 字节的 base64）
   - `annotations`：全部标注的矢量数据（类型、坐标、颜色、线宽等）
   - `frame`：边框装裱参数（背景/间距/圆角/阴影）
   - `original` + `crop`：未裁剪原图与当前选区（裁剪可逆，可重新选择）

重新打开时检测到该元数据即还原编辑现场；普通 PNG 则按普通图片打开。
马赛克不作为像素写入底图，而是作为矩形标注保存、渲染时实时像素化，
因此马赛克区域同样可以移动、删除、重新框选。

## 开发环境运行

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

## 自检

```bat
.venv\Scripts\python.exe main.py --selftest
```

离屏（不弹窗）验证全部工具、变换、SVG 图标渲染、保存与二次编辑链路，
输出 `SELFTEST PASS` 即正常。

## 打包 exe

双击运行 `build_exe.bat`，产物在 `dist\imgReEditor\imgReEditor.exe`。
可用 `imgReEditor.exe --selftest` 验证打包结果。

## 制作安装程序

1. 安装 [Inno Setup 6](https://jrsoftware.org/isdl.php)（或 `winget install JRSoftware.InnoSetup`）
2. 先运行 `build_exe.bat` 生成 `dist\imgReEditor\`
3. 用 Inno Setup 打开 `installer.iss` 编译，或命令行（路径按实际安装位置调整）：

```bat
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

安装程序输出在 `installer\imgReEditor_Setup_1.0.0.exe`。

> `installer.iss` 引用的简体中文语言文件 `ChineseSimplified.isl` 已随项目提供
> （来自 Inno Setup 官方翻译，Inno 默认安装不带它）。

## 目录结构

```
main.py               入口（支持 --selftest，应用保存的主题）
app/
  main_window.py      主窗口、SVG 图标工具栏、主题切换、文件操作
  canvas.py           画布：工具交互、裁剪/翻转/旋转、马赛克渲染
  annotations.py      标注数据模型、绘制、几何变换
  frame.py            边框装裱渲染（渐变背景/圆角/阴影）
  frame_dialog.py     边框设置对话框（实时预览）
  icons.py            SVG 图标库（运行时按主题色渲染）
  io.py               PNG 读写与可编辑元数据嵌入
tests/selftest.py     离屏自检
build_exe.bat         PyInstaller 打包脚本
installer.iss         Inno Setup 安装包脚本
```
