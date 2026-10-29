<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-dark.svg">
    <img src="docs/images/logo-light.svg" alt="hitlcad" width="360">
  </picture>
</p>

<p align="center"><a href="README.md">English</a> · 简体中文</p>

hitlcad 是一个 human-in-the-loop 的 CAD 工具。在 VS Code 里让 Claude Code 或 Codex 用 Python 写 CAD，
每次保存，hitlcad 都会自动 build、做测量，并在代码旁边显示 3D 模型。想改哪里，直接在模型上指出来就行，
不用费劲描述：点一个面，Agent 就知道你说的是哪个面；在上面钉一条批注（pin），Agent 就会去改代码。

<table>
<tr>
<td width="57%"><img src="docs/images/vscode-extension.png" alt="VS Code：右边是和 Codex 的对话，3D 编辑器里是它做的机械臂"></td>
<td width="43%"><img src="docs/images/point-and-pin.gif" alt="在 3D 编辑器里选中一个面，钉上一条批注"></td>
</tr>
</table>

## 工作方式

1. 在 3D 编辑器里看 build 结果，点 **Place pins**，再点模型上要改的地方，写下想要的效果，
   比如 *喷嘴出口收窄到 20 mm*。
2. 你发给 Agent 的下一条消息会自动带上这个 pin，以及它在哪个零件、哪个面上。所以说一句“处理一下我的 pin”
   就够了，什么都不用复制。
3. Agent 修改对应零件的代码。保存后自动重新 build，整个装配体再检查一遍。
4. pin 会跟着零件挪到新的 build 上。Agent 改完会把它标成 resolved，你在原来钉的位置就能看到改动；
   不满意就重新打开。

不写批注也一样：点一个面、一条边或一个零件，然后说“这个孔加大 0.5 mm”，Agent 就知道是哪个孔。
Agent 具体收到了什么，[下面](#一次完整的会话)有示例。

## 一次完整的会话

比如你想要*一个给电脑除尘的手持吹风机：750 W 无刷电机，打印风道，PETG，手枪式握把*。
这就是 [handhold-fan](examples/handhold-fan) 这个例子。

Agent 不会一上来就画几何。它先写清楚这个产品由哪些零件组成、怎么装在一起：打印件定好材料，
外购件从目录里选，每个连接都写明是哪两个特征连在一起：

```jsonc
"impeller":     { "role": "axial fan rotor on the motor shaft", "material": "PETG",
                  "connectors": { "bore": { "kind": "hole" } } },
"handle_screw": { "catalog": { "designation": "ISO 4762 M3 × 10" }, ... },

{ "type": "cylindrical", "a": "motor.shaft", "b": "impeller.bore", "fit": { "clearance": [0.0, 0.15] } },
{ "text": "Impeller blade tips clear the duct",
  "check": { "kind": "clearance", "between": ["impeller", "duct"], "min": 0.4 } }
```

接着每个零件写一个 Python 文件。每次保存，改过的零件会重新 build，整个装配体也会再检查一遍。
Agent 在这一步难免出错，比如把叶轮直径写死成 70 mm，而不是从参数里读。build 结果会是这样：

```
✗ interference duct+impeller: duct and impeller overlap by 91.11 mm³ in 7 pieces within x 4.00 … 22.00, y -34.20 … 35.00, z -35.00 … 35.00, about 0.25 mm deep (also: motion motor.shaft+impeller.bore, requirement 'Impeller blade tips clear the duct')
? requirement Intake guard is finger-safe: no opening wider than 7 mm: no check: the human judges it
1 problem (2 failing checks), 1 unverified; 130 checks pass
```

Agent build 完读到的就是这段输出：一个错误一行，同一个错误连带失败的其他检查也写在这一行里。所以不等你看，
它已经把叶轮改好、重新跑了一遍。再比如电机螺孔只有 6 mm 深，却选了 M3 × 12 的螺钉，四颗螺钉合成一行
`✗ fastener motor_screw.seat:motor_mount.motor_screws>motor.screw_holes ×4 at [0, 1, 2, 3]`，说的是
`bottoms out: it reaches 8.50 mm past the opening of motor.screw_holes[0], where motor stops it at 6.00`。

全部通过之后，就轮到你了。在开头的动图里，你点了风道的安装孔，又在喷嘴上钉了一条批注，
下一条消息就会带上这些：

```
Selected in the 3D view:
1. duct.mount_bore: Cylinder face r=39.42 mm, axis (1.00, 0.00, 0.00), area 17813.0 mm² (face 5 of duct)

Open pins (resolve them with resolve_pins once addressed):
- Cone face, axis (-1.00, 0.00, 0.00), area 8803.8 mm² (face 3 of nozzle): Narrow the nozzle to a 20 mm outlet
```

所以“把那个孔稍微加大一点”指的就是某个零件上确定的某个面，Agent 不用猜。零件重新 build 之后 pin 还在，
直到 Agent 把它 resolve 掉。

## 都检查些什么？

拿吹风机来说，每次 build 检查 134 项：

![吹风机的 Results 页](docs/images/design-results.png)

下面引用的都是这次 build 的实际输出。有检查没通过时，这一页先列问题，和 Agent 读到的那几行一样：
同一个错误导致的几项失败合成一条，阵列里的多个副本也合成一条。

- **Parts**（26）：每个零件都是合法的实体，网格闭合。
- **Connectors**（40）：Agent 声明的每个特征都能在几何体里找到并量出尺寸：
  `duct.mount_bore: Ø73.850 × 58.50 deep`。
- **Mates**（27）：连在一起的特征对得上，扣掉打印机的补偿量之后，配合间隙和要求一致：
  `fit 0.200 mm (hole Ø73.850, shaft Ø73.500, after process allowances (0.15 hole, 0 shaft)), needs 0.05 … 0.5`。
- **Fastener sets**（10）：每颗螺钉都能落座、穿过通孔、吃上螺纹：
  `motor_screw[0].seat through motor_mount.motor_screws[0] into motor.screw_holes[3]: seated, clears, engages 4.50 mm`。
- **Coverage**（1）：没有悬空的零件：`all 24 instances connect to duct`。
- **Interference**（1）：零件之间互不干涉，除非连接本来就要求重叠，比如热熔进风道的螺母嵌件；
  连接本来就要求的 6 处重叠会列出来，但不算作检查。
- **Motion**（1）：运动件在整个行程里扫一遍，看会不会撞：
  `motor.shaft ↔ impeller.bore moves through a full turn without collision (nothing in its reach meets its sweep)`；
  被其他连接固定住、不会动的 4 个 mate 会列出来，但不算作检查。闭环的 mate（四连杆、行星齿轮）会在
  环路求解器解出的每个位置上检查；你在面板里看它动一遍，确认后才算通过。
- **Manufacturing**（21）：每个零件按自己的加工工艺和摆放方向检查，比如壁厚
  `duct: thinnest wall 2.49 mm (minimum 0.8 mm)`，还有悬垂和热床接触面。FDM、光固化、CNC、钣金、
  注塑各有一套规则。
- **Requirements**（7）：设计说明（brief）里的需求逐条测量，运动过程也算在内：
  `impeller to duct: 0.73 mm (needs ≥ 0.4) in a full turn of motor.shaft+impeller.bore`。
  像“进风口护网不会伤到手指”这种程序判断不了的，就留给你来判断。

其他内容见[装配模型文档](docs/assembly-model.md)：ISO 286 配合、公差累积、齿轮关系、多配置。

## 示例

每个示例都会先拆开、再装回去，然后隐藏外壳，露出里面的结构。

<table>
<tr>
<td width="50%"><a href="examples/handhold-fan"><img src="docs/images/tour-fan.gif" alt="吹风机拆开，再隐藏风道、护网和喷嘴，露出电机和叶轮"></a></td>
<td width="50%"><a href="examples/nas-case"><img src="docs/images/tour-nas.gif" alt="NAS 机箱拆成各个打印件，再隐藏后壳和侧板，露出内部"></a></td>
</tr>
<tr>
<td valign="top"><a href="examples/handhold-fan"><b>手持吹风机</b></a><br>就是上面演示的那个：一台 750 W 电机加 13 个零件，算上螺钉和嵌件一共 24 个实例，134 项检查。build123d。</td>
<td valign="top"><a href="examples/nas-case"><b>NAS 机箱</b></a><br>能装四块 3.5 寸硬盘的 micro-ATX NAS，拆成九块打印，每块都放得进 256 mm 的热床，用 32 颗 M3 螺钉配热熔螺母固定。97 个实例，435 项检查。build123d。</td>
</tr>
<tr>
<td><a href="examples/robot-arm"><img src="docs/images/tour-robot.gif" alt="机械臂拆开，再绕底座和肩关节转动"></a></td>
<td><a href="examples/happydog"><img src="docs/images/tour-dog.gif" alt="小狗摆件拆成各个打印件"></a></td>
</tr>
<tr>
<td valign="top"><a href="examples/robot-arm"><b>机械臂</b></a><br> 七个连杆、六个旋转关节，每个关节都在完整行程里做过碰撞检查，还导出了仿真用的 URDF。800 × 390 × 860 mm，51 项检查。build123d。</td>
<td valign="top"><a href="examples/happydog"><b>开心小狗</b></a><br>带配色的小狗摆件，分三块打印：头插在一根销上，销的配合直接在网格上测；尾巴插在屁股上。在 Blender 里雕的，可以用滑块调头的倾斜、耳朵张开的角度和尾巴摆动。</td>
</tr>
</table>

每个示例就是一个文件夹，复制到项目的 `designs/` 下就能打开，也可以直接在命令行里 build：

```bash
hitlcad build examples/handhold-fan --out build-out
```

## 用你自己的 Agent，文件都在你手里

创建项目时，hitlcad 会给 Claude Code 和 Codex 装好工具和 skills，再加一个 hook，用来把你选中的东西和 pin
传过去。其他的照常用就行。

一个设计就是一个普通文件夹，直接提交到 git，用 diff 看 Agent 改了什么：

```
handhold-fan/
  design.json       零件、连接、参数、需求、设计说明
  interfaces.py     两个零件共用的尺寸，只算一次（比如孔和配合的轴）
  parts/duct.py     一个零件一个文件，在自己的坐标系里、按实际加工方式建模
  parts/...
  assembly.py       每个零件放在哪
  build/            每个零件的 STEP 和 STL，以及设计要求的导出；自动生成，不进 git
```

每个零件都会单独导出 STEP 和 STL，可以直接拿去打印或发给机加工厂。设计可以要求导出整个装配体的
STEP，它保留了装配结构，在别的 CAD 软件里打开是一个个零件和子装配体，而不是一整坨；需要的话还能导出
BOM、3MF，机器人项目可以导出 URDF 或 SDF。

面板上的功能比动图里多：参数滑块、任意两个版本对比、装配关系图、BOM 和设计说明。

![Structure 页：吹风机有哪些零件、怎么连在一起](docs/images/design-structure.png)

## 快速开始

1. 下载 [release](https://github.com/blne-team/hitlcad/releases)，解压后运行 `./install.sh`。
   系统要求、Windows、远程工作区和设置等细节见 [INSTALL.md](INSTALL.md)。
2. 在 VS Code 里运行 **hitlcad: New Design Project**，打开它生成的文件夹。
3. 让 Agent 给你做点东西。

## 更多文档（英文）

- [INSTALL.md](INSTALL.md)：安装、项目配置、设置、问题排查
- [docs/assembly-model.md](docs/assembly-model.md)：一个设计包含哪些内容，每部分怎么检查
- [docs/architecture.md](docs/architecture.md)：hitlcad 内部是怎么工作的
- [docs/plugins.md](docs/plugins.md)：插件（hitlcad 自带的词汇也是插件），以及每个错误如何呈现
- [docs/development.md](docs/development.md)：参与 hitlcad 本身的开发
- [docs/roadmap.md](docs/roadmap.md)：接下来的计划

## 许可证

[MIT](LICENSE)
