# 可移植排版与检查模块

## 职责与目录

优先复用 `scripts/paper_pdf.py` 及旁边的 `scripts/paperpdf/` 包。模块负责排版、指定区域裁剪、字体预检、自动核验、缓存渲染和交付检查；**不负责翻译、OCR、阅读顺序判断、自动判断裁剪框或科学审校**。正文、参考文献、字体路径、图表区域及符号定义全部从单篇论文的配置读取。不要把论文标题、页码、32条参考文献等特例写进模块。

整个skill文件夹可以复制到其他项目的 `.agents/skills/translate-paper-pdf/`。不依赖安装位置、当前工作目录、用户名或盘符；不打包商业字体。已有项目中的技能若位于其他位置，更新其现有目录即可，不擅自迁移或创建重复副本。

所需Python库见 `scripts/requirements.txt`。先发现可用解释器和已装依赖，缺什么再安装；不要每篇重新安装。正常使用只需CLI，编程时可把 `scripts/` 加入Python搜索路径并导入 `paperpdf.config.Paper`、`paperpdf.build.build` 等。构建是同步操作，不在同一进程并发构建：混排换行补丁在单次构建期间启用，退出时恢复。

## 单篇论文输入

把配置、内容与清单放入论文的 `tmp/`。所有相对路径（包括图像与字体）**统一相对配置JSON所在目录**解析，不相对清单文件、不相对shell工作目录。

`paper.json`示例：

```json
{
  "schema_version": 1,
  "blocks": "translation_blocks.json",
  "assets": "crop_manifest.json",
  "source_blocks": "source_blocks.json",
  "source_pdf": "../original.pdf",
  "output": "../paper_中文_v1.pdf",
  "work_dir": "pdf_work_v1",
  "title": "实际中文标题",
  "author": "原作者姓名",
  "sample_ids": ["title_en", "title_cn", "abstract", "method", "fig1"],
  "symbols": {
    "I_MON": {"text": "I", "sub": "MON", "italic": true},
    "Q_v": {"text": "Q", "sub": "v", "italic": true}
  },
  "expected_counts": {"figure": 1, "table": 0, "equation": 0, "reference_entries": 1}
}
```

数字和示例ID只示范结构，必须按当前论文填写。`expected_counts`从源文清点，不从译文倒推。正常任务提供`source_blocks`，模块校验原文和译文ID一一对应；来源块的`text`、`pages`、`regions`等由提取阶段维护，模块不据此判断译文语义正确。单个参考文献块的来源文本包含全部对应条目。

内容文件是按阅读顺序排列的JSON数组。支持：

- `title_en`、`title_cn`、`meta`、`heading`、`body`、`abstract`、`small`：`id`、`type`、`text`。文本按普通文本处理，XML字符自动转义；实际换行代表新段落，长段落不要按原PDF行宽手动折行。
- `figure`、`table`：`id`、`type`、`asset`、`caption`、`notes`。两种类型均要求中文图／表注和图内文字对应说明。`notes`只写内容，模块自动加“图中说明：”／“表内文字对应：”并独立排段。无英文标签的图也应说明坐标、符号含义。
- `equation`：`id`、`type`、`asset`；原式及编号在截图内，不从乱码提取文本重排。
- `references`：`id`、`type`、可选标题`text`和逐条完整英文字符串数组`entries`。先人工核对并修复提取断词；模块不机械删除行尾连字符，也不限定文献数。

示例：

```json
[
  {"id":"title_en", "type":"title_en", "text":"Actual English Title"},
  {"id":"title_cn", "type":"title_cn", "text":"实际中文标题"},
  {"id":"abstract", "type":"abstract", "text":"完整中文摘要。"},
  {"id":"method", "type":"body", "text":"变量I_MON与Q_v；保留负号−0.44和字面量data_id。"},
  {"id":"fig1", "type":"figure", "asset":"fig1", "caption":"图1 完整中文图注。", "notes":"N → 北纬；E → 东经。"},
  {"id":"refs", "type":"references", "text":"参考文献", "entries":["Author A (2020) Complete original reference."]}
]
```

`symbols`是当前论文的精确匹配规则；`text`为基符号，可选`sub`、`super`、`italic`。无需上下标的普通下划线不自动转换。Unicode上标数字和负号按明确映射排版，核验只采用模块实际输出的字符表示，不通过删除所有下划线或统一正负号来“消除差异”。避免过于宽泛的单字母符号规则误匹配英文单词。

## 字体与页面配置

默认宋体、楷体、Times New Roman及其粗体／斜体／粗斜体。依次检查配置`font_dir`、环境变量`PAPERPDF_FONT_DIR`和常见系统字体目录。缺字体立即报错，不能静默替换。跨平台推荐显式配置：

```json
{
  "fonts": {
    "song": {"path":"fonts/simsun.ttc", "index":0},
    "kai": "fonts/simkai.ttf",
    "roman": "fonts/times.ttf",
    "bold": "fonts/timesbd.ttf",
    "italic": "fonts/timesi.ttf",
    "bolditalic": "fonts/timesbi.ttf"
  },
  "layout": {
    "width_pt":595.2756, "height_pt":841.8898,
    "margin_pt":56.6929, "body_size_pt":10.5, "leading_pt":15.75,
    "oversize":"page"
  }
}
```

字体目录仅表示本地配置方式，不表示将字体加入skill发布包。替代字体须先取得用户确认，再显式指定路径。默认A4、20 mm页边距；所有尺寸单位为PDF点。裁剪仍按原框和 dpi 保留原分辨率；生成 PDF 时图表允许按源尺寸的 80%–120% 等比缩放。可在图表清单资产中设置可选 `scale`（默认 1.0，合法范围 0.8–1.2）。优先在 A4 页面内调整图像尺寸和版面以减少大段空白，检查图表及图注清晰可读；范围内仍无法容纳时再使用更大页面。`oversize:"error"`可改为明确报错后由排版者决定页面尺寸。特别长的图注可由排版者拆为标有“说明（续）”的正文块；自动模块默认把图及说明一起放到足够大的页面。

## 图表清单与裁剪

```json
[
  {"id":"fig1", "page":2, "bbox":[50,100,540,400], "dpi":600, "path":"crops/fig1.png"}
]
```

`page`从1开始；`bbox`为显示页面左上原点的`[x0,y0,x1,y1]`，单位pt，旋转／裁切页先预览实际显示坐标。每次都视觉确认框覆盖图、英文注释、面板标记等；不从本案例继承坐标。`crop`显式保留可见PDF注释并输出PNG。清单文件变化后不会悄悄覆盖已有不同裁剪，应使用新文件名／版本。第一次裁剪后放大查看所有图；像素数量校验不替代边界目检。

## 固定执行顺序

下面的`python`代表当前环境已经发现的解释器；`SKILL/scripts/paper_pdf.py`替换为本skill实际路径。

```text
python -X utf8 SKILL/scripts/paper_pdf.py crop PAPER/tmp/paper.json
python -X utf8 SKILL/scripts/paper_pdf.py preflight PAPER/tmp/paper.json
python -X utf8 SKILL/scripts/paper_pdf.py build PAPER/tmp/paper.json --sample
python -X utf8 SKILL/scripts/paper_pdf.py verify PAPER/tmp/paper.json --sample
python -X utf8 SKILL/scripts/paper_pdf.py render PAPER/tmp/paper.json --sample
```

已有裁剪PNG则跳过`crop`。打开`sample_render.json`列出的PNG，查看实际标题、摘要、最复杂上下标及图注，不只检查命令退出码。小样通过再运行：

```text
python -X utf8 SKILL/scripts/paper_pdf.py build PAPER/tmp/paper.json
python -X utf8 SKILL/scripts/paper_pdf.py verify PAPER/tmp/paper.json
python -X utf8 SKILL/scripts/paper_pdf.py render PAPER/tmp/paper.json
```

按`render_manifest.json`逐页查看。**实际查看通过后**才登记页码：

```text
python -X utf8 SKILL/scripts/paper_pdf.py review PAPER/tmp/paper.json --pages 1,2,3
python -X utf8 SKILL/scripts/paper_pdf.py delivery PAPER/tmp/paper.json
```

确实已看完所有页时可以用`--pages all`；不得用它代替查看页面。`delivery`重新核验PDF、输入、图像和目检记录，仅`ready:true`时允许交付。CLI检查失败返回非零状态，不继续后续交付步骤。输出已存在时构建拒绝覆盖；修订选择新输出版本和适当的工作目录。

## 防止本次问题再次出现

1. **混排换行**：按字体实际字宽断行；禁止中文闭标点位于行首、开标点位于行末；数字经纬度、英文词及基符号与上下标保持连接。不可拆文本比整行更宽时明确报错，不退回任意截断。运行期记录最终行文本，违规行使构建／验收失败。
2. **上下标假缺失**：pypdf按PDF内容流校验输出文字；pdfplumber只做几何检查。不要拿按纵坐标排序的提取文本判定上下标缺失。页码用PDF `/Artifact`标记排除，不用“删除开头数字”的正则，正文以数字开头也不会被误删。
3. **严格文本比较**：只忽略排版空白；上下标的文本表示在生成时明确记录。负号、下划线、标点、数字和段落内容均不能为过检而删除。修改内容流时保持字节编码，防止中文字体映射被破坏。
4. **图像身份与尺寸**：按图像像素内容、实际页面位置和物理宽高联合比对，容差0.1 pt。两幅同尺寸图互换也不能通过。检查实际图像数量、字符／图像越界、文字遮挡，以及两个PDF读取器的页数一致性。
5. **字体**：全量字符覆盖在写出前检查；实际使用字体须嵌入。未使用的ReportLab默认Helvetica资源不判为缺嵌入。所有西文及数学字符按西文字体片段处理。
6. **减少重复工作**：字体、排版与核验逻辑复用模块。裁剪按源文件与框参数缓存；页面预览按页面内容、资源、渲染器及倍率缓存，图片损坏则重新渲染。修改后只需重新查看清单中未有有效目检记录的页面；第一次仍查看全部页面。PDF、输入或模块代码变化会使旧自动验收失效，不能把旧报告当新结果。

完整交付仍需语义审校、原文数值和图表边界检查。以上拦截针对已经定位的排版／检查问题，不承诺任何新论文、字体或依赖版本都无需人工检查。

## 回归验证

修改模块或升级依赖后，在论文`tmp`或独立测试目录运行：

```text
python -X utf8 SKILL/scripts/tests/regression.py --work-dir TEST_OUTPUT
```

合成测试覆盖：中西文字体及完整文字、上下标、行首标点、数字开头正文、字面下划线、缺字／缺字体、换目录与不同工作目录、同尺寸图片互换、超宽图的原尺寸大页面、渲染缓存与目检交付门槛、输入变更失效、裁剪坐标及缓存。测试输出保留在指定目录；测试中的模拟目检登记仅用于验证状态转换，不代表实际文献目检。

已实测环境：Windows，Python运行时中的ReportLab 4.4.9、pypdf 6.10.0、pdfplumber 0.11.9、pypdfium2 5.13.0、Pillow 12.3.0。依赖范围见requirements；其他版本和操作系统需运行同样测试，不能把路径可移植等同于已经跨系统实测。
