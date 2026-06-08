# Plan: 用 Ruff 替换 flake8 + isort + mccabe

## 1. 结论

Gemini 给出的根因判断基本可信：当前环境已确认是 `CPython 3.11.9 on Windows`，
并且 `python -m flake8 ... -j 1` 能稳定快速返回结果，说明单进程路径正常。

但从仓库维护角度，单纯给 flake8 加 `jobs = 1` 只是本地绕过，不会减少工具链复杂度。
这个项目目前同时维护 `flake8`、`isort`、`mccabe` 三套配置和三段 CI 步骤，直接切到
`Ruff` 更干净，也更符合“彻底别再被 flake8 折磨”的目标。

## 2. 仓库现状

### 2.1 已确认的接入点

| 文件 | 当前状态 |
|------|----------|
| `pyproject.toml` | `dev` 依赖里包含 `flake8`、`isort`、`mccabe`，并存在 `[tool.flake8]` 与 `[tool.isort]` |
| `requirements.txt` | 也重复声明了 `flake8`、`isort`、`mccabe` |
| `.github/workflows/python-ci.yml` | CI 分别执行 `isort --check-only`、`flake8`、`python -m mccabe` |
| `src/utils/db.py` | 存在文件级注释 `# flake8: noqa: E501` |

### 2.2 已做的验证

- `python -m flake8 --version` 返回 `7.3.0 ... CPython 3.11.9 on Windows`
- `python -m flake8 src/cli.py src/main.py tests/test_main_parser.py ... -j 1` 能立即返回
- 当前代码至少已有一个真实 lint 问题：`src/main.py` 中未使用的 `parse_interval` 导入

这说明“flake8 行为异常”和“代码里也可能有真实告警”两件事同时成立。

## 3. 方案取舍

### 3.1 备选方案 A：最小修复

做法：

- 保留现有 `flake8`
- 在 `pyproject.toml` 的 `[tool.flake8]` 中新增 `jobs = 1`
- 可选地把 CI 命令也统一改为直接读取配置，不再手写一长串参数

优点：

- 改动最小
- 不需要迁移 lint 规则

缺点：

- 只解决 flake8 的 Windows 并发问题
- `isort` 和 `mccabe` 仍然是独立工具
- 本地与 CI 的 lint 入口依然分散

### 3.2 推荐方案 B：根治迁移到 Ruff

做法：

- 移除 `flake8`、`isort`、`mccabe`
- 新增 `ruff`
- 用 `ruff check` 接管 import 排序、pyflakes/pycodestyle、复杂度检查
- 保留 `black`、`pylint`、`mypy`

优点：

- 根除 flake8 多进程相关问题
- 统一三套工具为一套配置
- CI 更快、更短、更容易维护

缺点：

- 需要一次性迁移配置
- 需要修复现有 lint 问题，确保新规则集通过

## 4. 推荐实施内容

### 4.1 `pyproject.toml`

依赖调整：

- 删除 `flake8>=7.0`
- 删除 `mccabe>=0.7.0`
- 删除 `isort>=5.13`
- 新增 `ruff>=0.8.0`
- 保留 `black>=24.0`、`pylint>=3.0`、`mypy>=1.10`

配置调整：

- 删除 `[tool.flake8]`
- 删除 `[tool.isort]`
- 新增 `[tool.ruff]` 与 `[tool.ruff.lint]`

拟采用配置：

```toml
[tool.ruff]
line-length = 100
target-version = "py310"
src = ["src", "tests"]
exclude = [".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", "build", "dist"]

[tool.ruff.lint]
select = ["E", "W", "F", "I", "C90"]
ignore = ["E203", "E501", "W503"]

[tool.ruff.lint.mccabe]
max-complexity = 10
```

说明：

- `E/W/F` 对齐现有 flake8 检查面
- `I` 替代 isort
- `C90` + `max-complexity = 10` 对齐现有 mccabe 阈值
- 暂不额外引入 `B` 等新规则，先保持迁移风险最低

### 4.2 `requirements.txt`

同步依赖列表，避免和 `pyproject.toml` 出现两份 dev 依赖不一致：

- 删除 `flake8>=7.0`
- 删除 `mccabe>=0.7.0`
- 删除 `isort>=5.13`
- 新增 `ruff>=0.8.0`

### 4.3 `.github/workflows/python-ci.yml`

将这三步：

```yaml
- name: Check import ordering with isort
  run: isort --check-only src/ tests/

- name: Lint with flake8
  run: flake8 src/ --max-complexity=10 --extend-ignore=E203,E501,W503

- name: Check McCabe complexity
  run: |
    find src/ -name "*.py" -exec python -m mccabe --min 10 {} + || echo "McCabe: complexity issues found (threshold=10)"
```

合并为：

```yaml
- name: Lint with ruff
  run: ruff check src/ tests/
```

保留现有的：

- `black --check`
- `pylint`
- `mypy`
- `pytest`

不引入与本次问题无关的额外 CI 改造。

### 4.4 代码兼容性清理

需要一起处理的代码层细节：

- 修复 `src/main.py` 里未使用的导入
- 清理或移除 `src/utils/db.py` 中的 `# flake8: noqa: E501`

由于 `E501` 仍会全局忽略，这条文件级 flake8 注释大概率可以直接删掉，而不是迁移成
`# ruff: noqa`

### 4.5 验证步骤

执行并要求通过：

1. `ruff check src/ tests/`
2. `black --check src/ tests/`
3. `pytest tests/ -v`
4. 如有需要，`pylint src/ --fail-under=7.0 --max-line-length=100`

## 5. 风险控制

- 不切换到 `ruff format`，避免把格式化器也一起更换
- 不新增额外规则集，先尽量保持与当前 flake8/isort/mccabe 等价
- 如果迁移后发现 Ruff 暴露出大量历史问题，可先做一次 `ruff check --fix`，再手修剩余问题

## 6. 执行清单

| 文件 | 操作 |
|------|------|
| `pyproject.toml` | 切换 dev 依赖与 lint 配置到 Ruff |
| `requirements.txt` | 同步替换 dev 依赖 |
| `.github/workflows/python-ci.yml` | 合并 flake8/isort/mccabe 为 `ruff check` |
| `src/main.py` | 修复当前已确认的未使用导入 |
| `src/utils/db.py` | 移除或改写 flake8 专属注释 |
