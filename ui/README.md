"""界面层（PySide6）：设计系统 + 自绘控件 + 两块面板 + 主窗口。

目录约定
--------
* ``theme.py``        唯一颜色/尺寸来源（QSS 模板 + token），控件禁止写字面色值
* ``icons.py``        Lucide 风格 SVG 加载与染色（按需渲染任意颜色/尺寸）
* ``widgets/``        自绘控件（按钮/滑块/电平/录音钮/分段/徽章/开关…）
* ``main_window.py``  只做装配与信号路由
* ``soundboard_panel.py`` / ``recorder_panel.py``  左栏 / 右栏
* ``board_controller.py``  左栏交互控制器（导入/改名/删除/热键/试听）
* ``top_bar.py`` / ``status_bar.py`` / ``settings_dialog.py`` / ``tray.py``

设计系统（绝对不要绕过）
------------------------
``theme.PALETTE`` 是唯一颜色表，浅色值现在生效，深色值已写好但未启用：

| token | 浅色 | 用途 |
|---|---|---|
| canvas | #F7F8FA | 画布 / 列表底 |
| card | #FFFFFF | 卡片 / 面板 / 输入框 |
| hover / pressed | #F1F2F4 / #E8EAED | 悬停 / 按下 |
| border / borderStrong | #E5E7EB / #D1D5DB | 常规边框 / 强调边框 |
| text / textMuted / textFaint | #1F2328 / #6B7280 / #9CA3AF | 三级文字 |
| accent / accentHover | #3B82F6 / #2563EB | **唯一可交互色** |
| record | #EF4444 | 只给录音相关 |
| success | #10B981 | 只给"播放中 / 成功" |
| warning | #F59E0B | 只给"热键冲突 / 警告" |

其余 token：``SPACE``（4/8/12/16/24）、``RADIUS``（4/6/8/10/胶囊）、``TYPE``
（15/600 标题、13/400 正文、12/400 辅助、11/500 徽章、22/600 计时、11/500 音量）、
``SIZE``（标题栏 40、顶栏 48、底栏 32、右栏 340、行高 56…）、``ANIM``（120/200/1200/150ms）。

怎么用
------
取色与字号（自绘控件）::

    from ui import theme
    color = theme.color("accent")            # 跟随当前模式
    px, weight = theme.font_px("timer")      # (22, 600)

QSS 由 ``theme.stylesheet()`` 统一生成，``main.py`` 里 ``app.setStyleSheet(...)``。
需要新增样式时，**在 theme.py 的模板里加**（用 ``{accent}`` 占位符），
不要在控件里写 ``setStyleSheet("background:#3B82F6")`` —— 验收脚本会扫描字面色值。

切换主题（深色待实现）
----------------------
1. 把 ``theme.MODE`` 改成 ``"dark"``，或调用 ``theme.stylesheet("dark")``；
2. 自绘控件已经全部走 ``theme.color(key)``，会自动跟随；
3. 托盘/窗口图标用 ``ui/icon.pixmap(size, recording)``，同样取 token；
4. 剩余待补的点：``PALETTE`` 里深色值属于"可用但未细调"，需要一次目视校准。

自定义控件怎么复用
------------------
所有控件都在 ``ui/widgets/``，且**不依赖业务对象**（不认识 Clip/Engine）：

| 控件 | 关键 API | 信号 |
|---|---|---|
| ``IconButton`` | ``setIconName`` / ``setVariant(ghost\|danger\|accent)`` / ``setLoading`` | 继承 ``clicked`` |
| ``ThinSlider`` | ``setDimmed`` / 标准 ``QSlider`` API | ``valueChanged`` |
| ``Toggle`` | ``setChecked`` / ``isChecked`` | ``toggled(bool)`` |
| ``LevelMeter`` | ``setLevel(peak, rms)`` / ``setActive`` / ``reset`` | — |
| ``RecordButton`` | ``setRecording`` / ``isRecording`` | ``clicked`` |
| ``Segmented`` | ``setItems`` / ``setCurrent`` / ``setItemEnabled`` / ``setAllEnabled`` | ``changed(id)`` |
| ``HotkeyBadge`` | ``setHotkey`` / ``setState(unset\|bound\|capturing\|conflict\|failed)`` / ``startCapture`` | ``binding_requested`` / ``binding_changed(str)`` / ``binding_cancelled`` |
| ``Spinner`` | ``start`` / ``stop`` | — |
| ``DropOverlay`` | ``setText`` | — |
| ``EmptyState`` | ``setTexts`` / ``setIcon`` | ``action_requested`` |
| ``SearchBox`` | ``text`` / ``setText`` / ``clear`` | ``text_changed(str)`` |
| ``TitleBar`` | ``set_maximized`` | ``minimize_requested`` / ``maximize_requested`` / ``close_requested`` |
| ``TrimDialog`` | ``values() -> (start, end)`` | 标准 ``QDialog`` |

约定：
* 控件只发"意图"信号，不碰配置/引擎/文件；
* 颜色、尺寸、圆角一律从 ``theme`` 取；
* 需要新图标时，在 ``resources/icons/`` 放一个 ``viewBox="0 0 24 24"``、
  ``stroke="currentColor"``、``stroke-width="1.5"``、``fill="none"`` 的 SVG，
  ``icons.pixmap(name, size, color)`` 就能自动染色（缺失图标返回空 pixmap，不会崩）。

无边框窗口
----------
``main_window.py`` 用 ``FramelessWindowHint`` + 半透明底 + ``QWidget#Shell`` 画圆角；
拖动/缩放优先走 Qt6 原生 ``windowHandle().startSystemMove()/startSystemResize(edges)``
（保留系统吸附与命中区），失败才退回手动移动；最大化时外壳切 ``#ShellMaximized``
（去圆角，避免屏幕边缘留缝）。

验收自检
--------
* ``python tools/selftest.py``：核心层（不含界面）
* ``$env:QT_QPA_PLATFORM="offscreen"; python tools/selftest_ui.py``：界面层
  （边缘命中 / 鼠标事件 / 控件绘制 / 导入-录音-保存链路 / 无 emoji）

踩过的坑（别再踩）
------------------
* ``Qt.Edge`` / ``Qt.KeyboardModifier`` 这类是 **Flag 枚举，不能 ``int()``**，
  判断"有没有"用 ``bool()``，组合用 ``|``。曾因此导致每次左键点击都抛
  ``TypeError: int() argument must be ... not 'Edge'``（界面看起来正常，只在日志里报）。
* QSS 没有 ``outline``，也不支持 ``box-shadow``：焦点环要自绘，阴影用
  ``QGraphicsDropShadowEffect``（只给卡片/弹窗，别给整窗）。
* ``QSetting`` 式的"局部 setStyleSheet"会破坏主题一致性，一律写回 ``theme.py``。
