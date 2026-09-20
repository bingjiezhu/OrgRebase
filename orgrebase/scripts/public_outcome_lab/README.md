# 公共 retail 状态实验

这组脚本把固定的 tau2-bench retail task `113` 映射到
`oac.retail.cancellation-review/v0.1`，调用现有 `OutcomeLab`。它是实验入口，不是生产连接器、
第二个 OAC 执行器，也不是完整 tau benchmark。四个系统使用相同种子、请求、预算和状态 oracle，
唯一比较变量是明确记录的角色范围。producer、run 和 replay 共享同一基线角色定义并重算，
不接受保留系统名称却偷换角色范围的归档。每个系统实际运行两次，另保留一个
“组织计划 ACCEPT、执行目标 FAIL”的反例。

## 固定范围与可信输入

- 上游固定为 `sierra-research/tau2-bench` commit
  `672227c6b6676edc20d57ea53b7000262aae77b9`。`pins/tau-source.json` 固定 270 个源码/数据文件、
 原始 archive SHA 与 MIT LICENSE 字节；本地旁放的自签 manifest 不能替换这些 pins。
- 支持原 task 113 的明确请求和模拟确认。候选函数只收到公共 user scenario 和六次实际只读
  toolkit 结果；oracle 另从上游 `evaluation_criteria` 与 reset seed 派生，不作为候选输入。
- 只映射两个 pending order 的取消状态、取消理由和信用卡 refund ledger。没有实际退款、
 真实用户模拟、自然语言断言打分或模型调用。保留上游 `NL_ASSERTION` 为未支持项，不能把局部
 DB projection 写成全任务/全 benchmark 通过。
- 组织 nodes、roles、rules 是本项目声明的实验映射，具有独立来源标签，不是数据集 Gold。
 public `compile_change(..., profile=...)` 与 public verify 共用现有内核。OAC ACCEPT 不提供
 toolkit 凭据；controller 另给一次性、精确请求、work unit、角色、证据及预算授权。
- `PASS/FAIL/UNKNOWN` 六维直接来自核心状态/轨迹重算；未知不会变成成功。所有八次比较结果和
 第九个失败反例都在输出目录中保存。

## 独立环境与平台边界

当前实验 pins 对应 **macOS arm64、Python 3.12.13**，worker 通过 `/usr/bin/sandbox-exec`
运行，OS 拒绝 `network*` 与 `file-write*`。缺少该能力时明确拒绝，没有“不加隔离继续”的 fallback。
worker 以 `-I -S -B` 启动，跳过 `.pth` 与 `sitecustomize`，仅显式加入独立 toolkit 的
`lib/python3.12/site-packages`。只接收有界 JSON reset/snapshot/identity/已列名的四个工具请求；
不接收 Python、callback、shell 或任意工具插件，不继承产品凭据和 provider 环境变量。

75 个 toolkit 发行包只在独立 tau venv。`tau-requirements.txt` 固定版本，
`tau-environment-macos-arm64-py312.json` 固定 Python binary、安装代码与依赖逐包文件闭包。
实际打开的 seed 缓冲也与固定源码摘要核对，reset 不会随后另读可能变动的同名文件。
其中不绑定 pyc、生成脚本绝对 shebang、RECORD/direct_url 等安装位置元数据。操作者、解释器和
只读安装目录属于受信实验环境；这不是对恶意宿主/被替换解释器的远程证明。改变实现字节需要
新环境资格和新 pins；不能只改版本名掩盖安装差异。产品依赖和产品 venv 不安装这些包。

## 准备路径与依赖

以下变量由运行者指定；所有输出目录必须尚不存在。已有合格的上游和独立 venv 可直接复用。
不要把产品的 venv 设为 `TAU_PYTHON`。

```sh
PRODUCT_ROOT=/path/to/orgrebase
PRODUCT_PYTHON=/path/to/product-venv/bin/python
OAC_SOURCE=/path/to/current-oac-source
OAC_PYTHON=/path/to/oac-venv/bin/python
TAU_SOURCE=/path/to/pinned-tau2-source
TAU_PYTHON=/path/to/tau-venv/bin/python
LAB_WORK=/path/to/new-lab-parent
mkdir "$LAB_WORK"
export PYTHONPATH="$PRODUCT_ROOT/src:$PRODUCT_ROOT/scripts"
```

如需从空目录准备上游与独立依赖，可在新目录执行下面的显式安装步骤。下载/安装不是实验动作，
完成后实验 worker 始终禁止网络。此次施工复用了已下载上游，未再次抓取。

```sh
git clone --no-checkout https://github.com/sierra-research/tau2-bench "$TAU_SOURCE"
git -C "$TAU_SOURCE" checkout --detach 672227c6b6676edc20d57ea53b7000262aae77b9
uv venv --python 3.12.13 /path/to/tau-venv
uv pip install --python "$TAU_PYTHON" --no-deps -r "$PRODUCT_ROOT/scripts/public_outcome_lab/pins/tau-requirements.txt"
uv pip install --python "$TAU_PYTHON" --no-deps --build-constraints "$PRODUCT_ROOT/scripts/public_outcome_lab/pins/tau-build-constraints.txt" "$TAU_SOURCE"
"$PRODUCT_PYTHON" -m public_outcome_lab check-source --tau-source "$TAU_SOURCE"
```

在新环境首次 `prepare` 时，实际安装的 tau 源码与全部依赖文件必须匹配 pins；源树和新环境
看似同版本但实际字节不同仍会拒绝。OAC 必须包含上述公开 retail profile。原 Supplier 默认入口
不能接收 retail 计划，不会为此添加旧执行兼容路径。

## 从准备到执行

每条成功命令返回 JSON `artifact_root`；将这个值独立记录，不从待验证目录自取可信根。
下面的 `PREPARED_ROOT`、`INPUTS_ROOT`、`EXPERIMENT_ROOT` 分别填对应命令实际输出。

```sh
"$PRODUCT_PYTHON" -m public_outcome_lab prepare \
  --tau-source "$TAU_SOURCE" --tau-python "$TAU_PYTHON" \
  --output "$LAB_WORK/prepared"

PREPARED_ROOT=sha256:REPLACE_WITH_PREPARE_OUTPUT
"$PRODUCT_PYTHON" -m public_outcome_lab inputs \
  --prepared "$LAB_WORK/prepared" --prepared-root "$PREPARED_ROOT" \
  --oac-source "$OAC_SOURCE" --oac-python "$OAC_PYTHON" \
  --created-at 2026-09-10T00:00:00Z --output "$LAB_WORK/inputs"

INPUTS_ROOT=sha256:REPLACE_WITH_INPUTS_OUTPUT
"$PRODUCT_PYTHON" -m public_outcome_lab run \
  --tau-source "$TAU_SOURCE" --tau-python "$TAU_PYTHON" \
  --prepared "$LAB_WORK/prepared" --prepared-root "$PREPARED_ROOT" \
  --inputs "$LAB_WORK/inputs" --inputs-root "$INPUTS_ROOT" \
  --oac-source "$OAC_SOURCE" --oac-python "$OAC_PYTHON" \
  --output "$LAB_WORK/experiment"
```

`inputs` 在单独的 OAC 解释器中编译并验证当前计划，记录实际 OAC 源码闭包；`run` 再由 public
CLI 独立验证 ACCEPT 证书。工具 grant 的 `work_unit_ref` 与 `work_unit_predecessors` 从
这份确切计划派生，并交核心复验；脚本不能省掉顺序或借另一个 role 的权限。

## 离线重放

```sh
EXPERIMENT_ROOT=sha256:REPLACE_WITH_RUN_OUTPUT
"$PRODUCT_PYTHON" -m public_outcome_lab replay \
  --bundle "$LAB_WORK/experiment" --expected-root "$EXPERIMENT_ROOT" \
  --tau-source "$TAU_SOURCE" --tau-python "$TAU_PYTHON" \
  --oac-source "$OAC_SOURCE" --oac-python "$OAC_PYTHON" \
  --output "$LAB_WORK/replay-verification"
```

离线重放不重执行 task 工具。它用同一已固定的 RetailDB 只做 reset/snapshot，验证上游原始
seed 的实际模型默认值规范化；不手写另一套 seed 转换器。随后校验 source commit/hash/license、
所有目录与输入根、候选只读发现、独立 oracle、当前 public OAC 证书，并调用唯一核心
`verify_outcome_receipt` 重算九份完整 state/trace/authority/budget/工作单元/六维结果。
只有调用者固定的归档根有效，重新封装摘要不能使内部矛盾成立。

输出包含 mapping、oracle、actual plan certificate、implementation 组件摘要、每份完整状态和
工具轨迹、所有拒绝/失败、reset receipt、只读验证结论以及精确文件 manifest。此验证证明该
有界归档的一致性和可复核性，不证明未记录的远端生产效果、真实用户效率或 ROI。

## 检查脚本

```sh
PYTHONPATH="$PRODUCT_ROOT/src:$PRODUCT_ROOT/scripts" "$PRODUCT_PYTHON" -m pytest \
  "$PRODUCT_ROOT/tests/test_public_outcome_lab_scripts.py"
```

脚本测试不需要将 tau 装入产品解释器；实际隔离 toolkit 验收由上述完整命令链单独完成。
源码/schema/依赖发生变化后，保留旧目录并新建实验坐标，禁止覆盖已有材料来制造相同记录。
