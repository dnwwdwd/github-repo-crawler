# GitHub Repo Crawler

`github-repo-crawler` 是一个给 Codex Agent 用的 GitHub 仓库搜索和检查 skill。

它把 GitHub Search、仓库协议补查、README、目录树和指定文件读取收在一个无状态脚本里。Agent 拿到这些材料后，可以按证据写出仓库用途、架构、运行方式、依赖、风险和待确认问题。

## 能做什么

- 用必填的 GitHub Query 搜索仓库。
- 选填 stars 最小值和最大值；两个值必须一起传。
- 返回 Search 自带的协议状态，不会因为缺少协议把仓库从结果里删掉。
- 对选中的仓库调用 GitHub license API，区分已声明、缺失和未知的协议状态。
- 读取 README、目录树，以及 Agent 明确指定的文件。
- 输出 JSON，便于 Agent 继续分析或接入其他流程。

它不保存数据，也不做去重、冷却、定时调度、业务过滤或消费队列。

## 安装

将仓库克隆到 Codex 的 skills 目录：

```bash
git clone https://github.com/dnwwdwd/github-repo-crawler.git \
  ~/.codex/skills/github-repo-crawler
```

重新打开 Codex 的任务，或刷新 skill 列表后，用 `$github-repo-crawler` 调用。

## Token 和限流

脚本按以下顺序读取环境变量：

1. `GITHUB_TOKEN`
2. `GH_TOKEN`

没有 Token 时仍会使用匿名 GitHub API 请求，并在 JSON 结果中返回 `rate_limit` 和限流提示。GitHub Search 使用独立且较低的配额，实际额度以接口返回值为准。

## 使用方式

搜索 Python CLI 项目，并限制 stars 范围：

```bash
python scripts/github_repositories.py search \
  --query 'topic:cli language:Python' \
  --stars-min 50 \
  --stars-max 500 \
  --sort updated \
  --order desc
```

不限制 stars 时，不传 `--stars-min` 和 `--stars-max`：

```bash
python scripts/github_repositories.py search \
  --query 'topic:cli language:Python'
```

检查一个候选仓库，读取 `package.json` 和 `docker-compose.yml`：

```bash
python scripts/github_repositories.py inspect \
  --repo owner/repository \
  --file package.json \
  --file docker-compose.yml
```

检查命令会补查仓库协议，并返回 README、目录树和指定文件。不要一次读取全部文件；从 README、目录树和部署配置开始，按问题追加目标文件即可。

## Agent 分析输出

skill 要求 Agent 针对检查过的仓库写 Markdown 报告，至少列出：

- 开源协议状态、SPDX ID 或协议名称，以及来源。
- 仓库用途。
- 架构、技术栈和运行信号。
- 可见的外部服务与依赖。
- 维护、运维、安全或文档风险。
- 用到的 GitHub 元数据、README、目录或文件路径。
- 证据不足时仍待确认的问题。

仓库里没读到的文件不能作为结论依据。

## 开发

项目只依赖 Python 标准库。运行校验和测试：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" .
python -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)
