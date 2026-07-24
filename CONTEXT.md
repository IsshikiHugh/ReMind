# Context: ReMind

Notes/todo printer tool for Phomemo M02 thermal printer.

**命名**：项目 = ReMind（receipt + mind），命令 = `remind`，Python 包 = `remind`，环境变量 = `REMIND_HOME`，app bundle = `ReMind.app`。编译产物与 `pip install -e .` 装出来的命令同名。GitHub 仓库 = `IsshikiHugh/ReMind`（public）。

## Glossary

- **Print Job** — 一次打印请求。输入为纯文字（未来可能扩展图片），输出为热敏纸上的一段内容。
- **Layout Template（排版套路）** — 固定的、非用户可配的排版规则。用户只提供纯文字内容，工具负责套用固定排版。渲染层已拆为 `render/` 子包（`raster.py` 光栅基元 + `text.py` 文字渲染），预留 layout/image 等模块生长空间；对外 API `from remind.render import render_text, Raster` 不变。
- **Transport（传输层）** — 与打印机之间的物理/协议通信（M02 走 BLE 蓝牙）。设计上必须与业务逻辑解耦。
- **广播 / advertising** — BLE 设备开机后持续自报家门的状态。M02 平时不"连着"，用的时候才被扫描并连接，用完断开。关机/休眠时不广播 → 扫不到。

## Printer Discovery

- **只有一台固定打印机，但不一定一直开机**。BLE 下"用时才连、用完就断"是常态，契合无状态 CLI。
- **TOFU（Trust On First Use）信任模型**：出于安全，不按名字裸扫（附近可能有别人的 M02，名字不可作信任凭据）。首次人工确认设备后记住其稳定标识符，后续只自动匹配这台已认识的设备。
  - **首次注册**（落地在 TUI 的 Devices → `+` 扫描面板）：扫描 → 列出附近设备 → 用户人工选中确认 → 写入配置 `{name, address/uuid, platform}`。唯一建立信任的时刻，所以只在 TUI 里、绝不在 CLI 里。
  - **后续打印**：扫描 → 只连配置里记住的标识符 → 匹配上才打印。
  - **标识符跨平台差异**：Linux 存 MAC（永久稳定）；macOS 只能存系统 UUID（同一台 Mac 上稳定，换 Mac 会变）。
  - **兜底：宁缺毋滥**。记住的标识符扫不到时（换 Mac / 系统重置 / 打印机没开机），报错并提示重新注册，**绝不自动信任名字碰巧一样的新设备**。
- **打印机没开机场景**：扫不到时须给出清晰错误（"请确认 M02 已开机"），不可卡死或抛底层蓝牙错误。

## Device Registration & Config（已落地为 lib 构件）

- **`config.py`（第四个模块，纯持久化，与三层解耦）**：`DeviceRecord(name/address/platform/order)` + `load_devices`/`save_devices`。
  - **格式 TOML**。路径最终定为 `$REMIND_HOME` → 仓库根（仅源码树且文件已存在）→ `~/.config/remind/config.toml`，详见"打包成单文件二进制"一节。`config.toml` 已 gitignore（本机状态 + 含设备 UUID，不提交）。
- **`register_device(device, order=)`**：把用户选中的设备写进配置。纯持久化、不做交互；按地址去重（重复注册原地更新）。order 缺省追加到末尾。
- **`discover(name_prefix=None)`**：扫描列出附近设备（不早停，等满窗口拿全量），供 CLI 做"人工选"。交互 UX 属 CLI 层。
- **多设备 + order**：可注册多台，各有 order（小=优先）。`scan_registered()` 一次性扫所有已注册地址，扫到 #1 即早停；返回在线设备中 order 最靠前的那台。`save_devices` 落盘时把 order 归一成 0..n-1，保证与界面/CLI 的序号一致。
- **`print_text` 已接上注册表**：只打给**已注册**设备。`address=None` 走 order 兜底（谁在线且最靠前就打给谁），传 address 则只找那一台。名字盲扫只剩 `transport.scan()` 供 debug 脚本用——名字永远不是信任凭据。
- **错误分两级**：`NoDeviceRegisteredError`（还没注册，去 TUI 注册）vs `PrinterNotFoundError`（注册了但没开机/不在范围），消息直接可以打给用户看。

## Print Config（已落地）

- **打印配置（纸宽 / 字号 / 上下左右 margin / 密度等）按设备记忆**：每台注册设备各自保存一套打印配置，不是全局单例。切换/使用某台设备时加载它自己的配置。
  - 落地为 `config.PrintConfig`，作为 `DeviceRecord.print_config` 存进 TOML 的 `[device.print]` 子表（跟在各自的 `[[device]]` 后面）。改 order / 删设备时配置跟着设备走。
  - **每个字段都真的驱动渲染**（不是摆设）：`paper_width_mm` 决定可用宽度（`usable_width_px`，硬件行宽恒为 48 字节），`font_size`/`margin_x` 决定折行，`margin_top/bottom` 决定留白，`density` 映射到热度，`feed_dots` 决定走纸。
  - 预览（`render/preview.py`）忠实反映当前配置：**折行直接调用真渲染器的 `wrap_lines`（同字体同宽度），所以屏幕上怎么断，纸上就怎么断**；纸宽/margin 按同一个 px→cell 比例缩放并 pixel round，所以小于一格的 margin 会合理地被舍掉。

## CJK 支持

- **字体不能用 Pillow 默认字体**（无中日韩字形，中文会变空框）。`render/fonts.py` 按候选表找系统字体：macOS PingFang（`.ttc` 第 3 个 face = SC）/ Hiragino / Arial Unicode，Linux Noto CJK / 文泉驿，都没有才退回默认。
  - **探测必须用"试着打开"而不是 `Path.exists()`**：macOS 的系统字体（如 PingFang.ttc）不在目录列表里，`exists()` 会返回 False，但 `ImageFont.truetype` 打得开。踩过这个坑。
- **折行规则**：中文没有空格，所以按"原子"拆——拉丁词整块、CJK 单字成块、空格是分隔符；拉丁按空格断，CJK 按字断，超长无空格串（URL）按像素砍。
- **禁则处理**：`。，」）…` 等不能出现在行首，会把上一行末尾拉下来。若上一行结尾是拉丁词，**整个词一起拉下来，绝不从字母中间切**（这里出过 bug：`report，` 被切成 `repor` / `t，`）。
- **终端预览按 cell 宽度算**（`rich.cells.cell_len`），CJK 是双宽字符，用 `len()` 会错位。

## Front Ends（已落地）

- **`tui.py`（人）**：单页三列（Menu / Content 预览 / Printer + 上下文面板）。**便签、config、device 全是 in-place 编辑，不开新页、也不离开 app**；改配置实时重渲染，enter 提交才落盘，esc 回滚。register / reorder / delete 只在 TUI。扫描是真 BLE，结果流式进表（`discover(on_found=...)` + queue + async worker）。
- **按键语义**：`l` = enter（确认/进入），`h` = esc（返回），与 `j/k` 配成 vim 手势。**`Input` 会吞掉所有可打印键**（`_on_key` 里 `event.stop()`），所以 h/l 在编辑框里根本传不到 binding——用 `ConfigInput` 子类覆写 `_on_key` 拦下这两个键（该框只收数字，不会误伤输入）。
- **便签编辑器 `NoteArea` 是上面那条的反面**：写便签时 h/l/j/k/数字/q **本来就该是字符**，所以 `TextArea` 吞掉可打印键正合适，一个都不用放行；出口只留 `escape`（默认 `tab_behavior="focus"` 不碰 escape，所以子类加个 binding 就够）。
- **编辑框 = 预览框**：`#note` 与 `#preview` 同一位置、同一边框、同一"纸张"底色，宽度都取 `_paper_cols() + 2`，`height: auto` 跟着内容长。视觉上就是"直接在纸上写"，也让 soft-wrap 的折行位置接近真实纸宽（`_refresh_preview` 为了不裁字会把框撑宽，编辑器故意只用**纸本身**的宽度）。
- **`cli.py`（机器）**：无任何二级交互。`--text`（`-` 读 stdin）、`--device <id|address|name>`、`list device` 输出 TSV；数据走 stdout、说明与错误走 stderr、失败 exit 1。无参数则起 TUI。
- **`--device` 的 id**：即 `list device` 的 1-based 序号（= order+1），reorder 后会变；因此 `resolve_device` 同时接受 address 与 name，脚本里想要稳定引用就用 address。
- `pyproject.toml` 提供 `remind` 命令（`pip install -e .`）。
- **怎么验 TUI**：逻辑用 `App.run_test()` + Pilot 断言；真终端里的**画面**用 **tmux**（`tmux new-session -x 104 -y 32 …` + `capture-pane`），**别用 pyte**——pyte 重放 Textual 的增量刷新会留下幽灵字符（框变宽时看着像旧边框没擦干净），tmux 和 Textual 自己的 compositor 输出则完全一致、干净。差点又把 pyte 的截图当成渲染 bug 的证据。
- **Pilot 的 `press()` 送不了全角标点**（如 `，`）：它按键名反查字符（`unicodedata.lookup("fullwidth_comma")` 查不到）就把 character 置 None，这个键直接被丢掉。真实终端的 XTermParser 手里一直有字符，所以这只是脚手架的限制——测试里直接构造 `events.Key(_character_to_key(ch), ch)` 即可。

## 编译成二进制（已落地，Nuitka）

- **启动慢的根因不是"没编译成机器码"**，是 PyInstaller **onefile 每次启动都把 ~20MB 解压到新的临时目录**，macOS 对新写出的可执行文件重做签名/安全校验，缓存按路径走 → 路径每次都变 → 每次都重付。实测：

  | 构建 | 首次 | 之后 | 体积 | 构建耗时 |
  |------|------|------|------|----------|
  | PyInstaller onefile | 5.0s | **5.3s 每次** | 20MB | ~12s |
  | PyInstaller onedir | 5.2s | 0.08s | 43MB | ~10s |
  | **Nuitka** | 0.9s | **0.055s** | 94MB | ~5min |

  只要文件固定在磁盘上，这笔开销就只付一次。**所以 Nuitka 若用 `--onefile` 会踩同一个坑**，必须用 app/standalone。
- **最终选 Nuitka**（`packaging/build.sh`）：真编译成机器码，启动最快，代码也不再是可直接反解的字节码。代价是构建 5 分钟、体积 94MB。
- **它不是"零依赖静态二进制"**：`remind` 可执行文件 64MB 是编译后的 Python 代码，但 libpython、PIL 的 `_imaging.so`、PyObjC 那些 `.so`/`.dylib` 仍然随包走（94MB 的大头是它们）。
- **⚠️ 用 `--mode=app-dist`，不是 `--mode=app`（踩过）**：Nuitka 的 `app` 只在 **macOS** 上是 app bundle，**其他平台上它就是 onefile** ——也就是上面那张表里最慢的那一档。`build.sh` 原来写死 `--mode=app`，在 macOS 上没问题，但一放到 Linux CI 上就会悄悄产出 onefile 包。`app-dist` 才是想要的语义：**除 macOS 外一律 standalone，macOS 上仍然出 bundle**。macOS 必须要 bundle 是因为 PyObjC 的 Foundation 不接受别的打包方式（Nuitka 直接 FATAL）；这反而是好事——bundle 自带 Info.plist，正好用 `--macos-app-protected-resource` 声明 `NSBluetoothAlwaysUsageDescription`。
- **Linux 产物结构**：standalone 出的是 `entry.dist/` 目录，`build.sh` 改名为 `dist/ReMind/`，同样软链 `dist/remind`，和 macOS 侧对称。
- **入口不能叫 `remind.py`**：那会在编译时和 `remind` 包重名互相遮蔽，所以用 `packaging/entry.py`，构建完再把 `entry.app` 改名 `ReMind.app`，并软链 `dist/remind` 供 alias/PATH 用（软链跟着重建走）。
- **config 路径必须先改，否则冻结后必坏**：原来是 `Path(__file__).parent.parent`（仓库根），打包后 `__file__` 指向 bundle/临时目录，onefile 下更是**退出即删**，注册信息全丢。现在按 `$REMIND_HOME` → 仓库根（仅源码树且已存在）→ `~/.config/remind/` 解析。**冻结检测要认两种**：PyInstaller 设 `sys.frozen`，Nuitka 设模块级 `__compiled__`。
- **实测编译版里都正常**：`--help`、config 解析、真 BLE 扫描（PyObjC/CoreBluetooth 加载正常）、pty 里跑起 Textual TUI 并 `qq` 正常退出。
- **Nuitka 4.1.3 对 Python 3.14 只是"实验性支持"**（构建时有 WARNING）。目前实测无问题，但升级 Python 或 Nuitka 后要重跑上面那套验证。
- **不能交叉编译**：macOS 的在 macOS 上编，Linux 的在 Linux 上编。字体**不打包**——运行时按路径找系统字体。
- **CI（`.github/workflows/build.yml`）**：因为不能交叉编译，四个目标各占一台 runner —— macOS arm64/x86_64 + Linux x86_64/arm64。push `v*` tag 建 release 并挂上 tar.gz；PR 只编 Linux x86_64 当"还编得过吗"的冒烟。**打 tar 不打 zip**：要保住 `dist/remind` 那个软链和可执行位。CI 用 Python 3.13 而非 3.14——3.14 在 Nuitka 这儿还是实验性支持，发布产物不适合当小白鼠。
- **不做 Windows**：`render/fonts.py` 的候选表里没有任何 Windows 字体路径，中日韩会退回 Pillow 默认字体变空框；bleak 的 WinRT 后端也从没在这个项目上验过。要支持得先补这两块，不是加个 runner 的事。

## 连接会话（TUI 专有）

- **动机**：扫描+连接要好几秒。对一次性命令无所谓，对"坐在里面用"的 TUI 是错的成本。所以 TUI 持有一条**热连接**，CLI 保持无状态一次性（脚本/cron 调用不留连接）。
- **落地为 `session.py`（第五个模块）**：`PrinterSession` 持有一个长开的 `BLETransport` + 一个 idle deadline。**故意不放进 core**——core 必须保持无状态，会话是 TUI 的选择。
- **规则**：进入 Edit 时**并行**连接；edit / print / 打字都 `touch()` 刷新倒计时；空闲 `IDLE_TIMEOUT_S = 300s` 后自动断开（长期占着打印机既耗电、又挡住别的设备配对）。
- **必须并行，串行不可接受**：第一版是"连上了才让人开始写"，等于把 BLE 的几秒直接怼到用户脸上——被否了。现在 `_edit()` 先把 `_warm_up()` 甩出去（独立 worker，`group="warm"`，失败静默——它是投机性的，真要紧时 Print 会正经报错），再立刻聚焦编辑框，一帧都不等。
- **连接失败绝不能挡住编辑**：打印机没开也要能写便签。
- **打字也要 `touch()`**（`on_text_area_changed`）：不然写一篇长便签，能把自己刚开的连接晾到过期。
- **电量是"活的"**：只有连着才有值，断开就显示 `??`，**没有缓存值可退**——`DeviceRecord.battery` 连同 `update_battery()` 已经删掉了。既然定下"UI 不许拿旧值冒充现状"，那个字段就只写不读，是死重量。旧 config.toml 里残留的 `battery = N` 会被忽略，下次保存时消失。设备列表里也只有真正连着的那台显示电量。
- **打印机可能自己挂断**（idle sleep / 走出范围），所以 `BLETransport` 接了 `disconnected_callback`，会话据此把自己标记为断开。
- **`core.print_to` 拆成 `render_for` + `send_job`**：`send_job` 在"别人拥有的连接"上跑一次任务，不负责连/断——这才让会话能用同一条连接连打多次。
- **⚠️ 会话持有的 `DeviceRecord` 是连接那一刻的快照，打印前必须重新读配置**（踩过）：`session.print()` 原来直接用 `self._record.print_config`，而用户在 Config 里改的是 TUI 内存里的另一个对象。**因为 Edit 会先连接，所以配置改动几乎必然发生在连接之后 → 字号/margin 永远不生效**（实测：48→20 打印字节数一模一样，12384 vs 12384）。现在 `print()` 先 `find_device(address)` 拿最新记录。CLI 不受影响，它每次都重新加载。
- **`_active()` 跟随会话实际连接的设备**，而不是永远 `devices[0]`：order 兜底会连到 #2，若面板/预览仍显示 #1 的名字和配置，就是在撒谎。`_resync_session` 相应改成"只有它的设备被删了才断开"。
- **`asyncio.Lock` 保护**：BLE 操作不能交错。但**扫描必须在锁外**——它慢达 timeout（10s），锁在里面的话 `close()`（进而 Quit）要等整个扫描结束，用户看到的就是"退不出去、Ctrl-C 也没用"（真踩过，pty 复现 6/6 必现）。现在 `ensure` 把扫描放在锁外、只在装配 transport 时短暂持锁；`close()` 会**取消**在途扫描而不是等它。
- **Quit 绝不能等蓝牙**：`asyncio.wait_for(session.close(), QUIT_DISCONNECT_S=1.0)` 兜底，超时就直接退——进程死了系统自然会断链，没理由让人对着冻住的屏幕干等。
- **退出编辑时再 `touch()` 一次**：编辑本来就算活动，所以即使写了超过 5 分钟、期间 deadline 已过，退出时刷新也是对的。
- **后台 worker 会比屏幕活得久**（退出时仍在跑的打印/会话 worker）。碰控件要接 `NoMatches`，否则 `WorkerFailed` 直接把 app 打崩。**不能用 `is_mounted` 当守卫**——`on_mount` 执行期间它还是 False，会把首次渲染吞掉（踩过）。

### 已废弃：外挂 `$EDITOR`（vim）那条路

Edit 最初是 `app.suspend()` + 起 vim 改临时文件。现在便签直接在 Content 框里编辑，这条路整个删掉了（`_suspended` / `_open_editor` / `_discard_suspended_output` 都不再存在）。留着教训，因为它们是 Textual 通用的坑：

- **⚠️ suspend 期间产出输出会把整个 app 锁死（踩过，最难查的一个）**：Textual 输出线程的队列是**有界的 `Queue(MAX_QUEUED_WRITES=30)`**，suspend 期间该线程停止消费。`Header(show_clock=True)` **每秒重绘一次**往里塞，30 秒后队列满，`put()` 直接**永久阻塞主线程（事件循环）**——不 resume（TUI 不回来）、不读键盘（Ctrl-C 无效）。现象是"在 vim 里待久一点，出来 TUI 就没了"，实测 10s 没事 / 30s 必挂，与队列容量精确吻合。修法是去掉 header 时钟 + 编辑期间主动清空该队列。**header 现在仍然没有时钟，但理由已经不成立了**（没有任何东西再 suspend），想加回来是安全的。
- **阻塞事件循环 = 并行全废**：`subprocess.call` 直接把循环冻住，warm-up 一起停摆；换成 `asyncio.to_thread(...)` 再 `await` 才真的并行（实测串行 4.5s → 并行 3.3s）。
- **等子进程要用 `asyncio.wait({task}, timeout=...)` 而不是 `sleep`**，否则子进程退出后还有一个窗口期，按 Edit 会被静默忽略。
- **worker 不能是 `exclusive`**：第二次触发会**取消**正卡在 `app.suspend()` 里的第一个，留下"已 suspend 但永不 resume"的 app。
- **查错教训**：追踪写入时 patch 了 `Driver.write`，但 `LinuxDriver` **覆盖了**该方法，日志为空，我据此错误地排除了"写入"方向。最后是 `sample <pid>` 的堆栈（主线程停在 `lock_PyThread_acquire_lock`）把方向拽回来的——**卡死类问题先抓堆栈，别靠猜**。
- **验伪记录**：曾怀疑是 Textual 在 suspend 期间仍重绘、把转义序列吐进 vim 画面。实测编辑期间泄漏 **0 字节**，`suspend_application_mode` 确实停了输出——方向是错的，队列锁才是唯一原因。

## Printer Status

- **电量是真的**：`1F 11 08` 写入 write char，打印机在 notify char（`0xff03`）回 `1A 04 <value>`；低电量是编码值（`A4→0 A3→3 A2→5 A1→10`），否则就是百分比。同理可查 paper / firmware / serial。
- **查询时机**：CLI 在打印后顺带查一次（连接已经开着，纸也出来了）；TUI 在会话建立时查一次、每次打印后再更新。查不到就是 `??`，不猜。

## Verified Facts (real hardware)

- **设备名 = `Mr.in_M02`**（不是纯 `M02`）。名字含 `M02` 的匹配可扫到，但印证 TOFU：名字只作粗筛，不作信任凭据。
- **macOS 地址 = 系统 UUID**（本机实测形如 `<8-4-4-4-12>` 的 UUID），非 MAC，符合预期。
- **BLE 写入必须带响应（`response=True`）**。用 write-without-response 会因无流控导致 M02 缓冲区溢出、**打印中途截断**。带响应后打印完整。这是关键坑。
- **M02 协议实测有效**：前缀 `10 ff fe 01` → `1b 40` → 热度 `1b 37 07 <heat> 02` → 光栅头 `1d 76 30 00 48 00 <行低><行高>` → 128B 分块 → 走纸 `1b 4a 08`。宽 48 字节 / 384px / 203DPI。
- **字号**：默认 48px（约 12mm 高）用户认可。字体偏细，热敏下可考虑加粗（待定）。

## Timing (real hardware, 单次冷启动)

| 阶段 | 实测 | 备注 |
|------|------|------|
| 阶段 | 优化前 | 优化后 |
|------|--------|--------|
| 扫描 | 8-10s（傻等满 timeout） | **0.8-3.7s**（发现即停） |
| 连接 | 1.8-14.6s | **1.2-1.8s**（直传 BLEDevice） |
| 发送打印 | 1.5-2.8s | 2.8s（带响应写入） |
| **总计** | **13-17s** | **~8s** |

- **两个提速关键**：(1) 回调式扫描"发现即停"，不等满 timeout；(2) 把 scan 拿到的原始 BLEDevice 直接传给 BleakClient，避免 macOS 上按地址连接时的二次发现（那次 14.6s 的元凶）。
- **扫描 timeout 定为 10s（`DEFAULT_SCAN_TIMEOUT_S`）**：8s 实测会偶发扫不到（BLE 广播间隔不规律，冷机更慢）。实测一次完整打印 9.6s——8s 就失败了。早停机制还在，所以正常情况下不会真等满。
- **等待时间要可见**：`print_text` 拆成 `find_printer` + `print_to` 两段，TUI 才能分别显示 `searching… 3.2s / 10s` 和 `printing… 1.4s`（Printer 面板里实时跳），失败提示也带上已等待秒数；CLI 同样在成功/失败信息里报耗时。
- **结论已定：~8s 足够，无需 daemon**，保持无状态 CLI。剩余波动在扫描（BLE 广播间隔物理特性），无 daemon 下已达极限。传输层仍解耦，未来若要 daemon 不需重写。

## Decisions

- **无状态 CLI 优先**：核心形态是一条命令 `remind`，内部完成 连接 → 打印 → 断开，每次调用独立。不强制引入常驻进程 / 网络协议。
  - 前提已验证：~8s（见 Timing），够用，**不做 daemon**。
- **传输层解耦**：蓝牙连接逻辑与业务逻辑（排版、内容处理）分离，以便未来可平滑切换到常驻 daemon 或 HTTP server 模式，无需重写。
- **技术栈：Python**。文字渲染用 `Pillow`（文字 → 黑白点阵图），BLE 用 `bleak`（跨平台异步），CLI 用 `typer`、TUI 用 `textual`。参考代码是 JS，但协议语言无关，翻译即可。

## Reference

- 协议知识来自 [transcriptionstream/phomymo](https://github.com/transcriptionstream/phomymo)。本地 clone 放在 `reference/phomymo/`（已 gitignore：是别人的代码，不随本仓库分发）。
