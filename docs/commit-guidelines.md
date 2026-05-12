# Git 提交规范

## 1. 目的

为了让项目的 Git 提交记录更加清晰、可读，方便后续查看历史、定位问题和协作开发，本项目采用简化版的 Conventional Commits 提交规范。

每次提交信息应尽量说明：

- 本次提交做了什么；
- 属于什么类型的改动；
- 影响了哪个模块或功能。

---

## 2. 提交格式

推荐使用如下格式：

```text
<type>(<scope>): <subject>
````

其中：

* `type`：提交类型，必填；
* `scope`：影响范围，可选；
* `subject`：提交说明，必填。

### 示例

```text
feat(memory): 添加记忆更新按钮
fix(api): 修复用户信息读取失败问题
docs: 更新项目说明文档
refactor(workflow): 简化记忆检索流程
```

如果改动很小，也可以不写 `scope`：

```text
fix: 修复启动失败问题
docs: 补充环境配置说明
chore: 更新依赖版本
```

---

## 3. type 类型说明

本项目常用以下几种类型即可：

| type       | 说明                | 示例                          |
| ---------- | ----------------- | --------------------------- |
| `feat`     | 新功能               | `feat(memory): 添加记忆更新入口`    |
| `fix`      | 修复问题              | `fix(ui): 修复按钮点击无响应`        |
| `docs`     | 文档修改              | `docs: 更新 README`           |
| `style`    | 代码格式调整，不影响逻辑      | `style: 格式化代码`              |
| `refactor` | 代码重构，不新增功能也不修 bug | `refactor(agent): 拆分对话处理逻辑` |
| `test`     | 测试相关              | `test: 添加记忆模块测试`            |
| `chore`    | 配置、依赖、构建等杂项       | `chore: 更新 package.json`    |

小项目中一般用好这几个就够了。

---

## 4. scope 范围说明

`scope` 用来说明本次提交影响的模块，可以根据项目结构自行填写。

常见示例：

```text
memory
agent
workflow
ui
api
db
docs
config
```

例如：

```text
feat(memory): 添加长期记忆表结构
fix(agent): 修复对话状态丢失问题
docs(workflow): 补充检索流程说明
```

如果不确定影响范围，或者改动比较零散，可以省略 `scope`：

```text
fix: 修复若干小问题
chore: 整理项目配置
```

---

## 5. subject 编写要求

`subject` 是对本次提交的简短说明。

要求：

1. 使用简短中文描述；
2. 尽量说明具体改动；
3. 不需要句号结尾；
4. 不要写得太泛。

推荐：

```text
feat(memory): 添加记忆更新按钮
fix(api): 修复空结果返回异常
docs: 添加本地启动说明
```

不推荐：

```text
update
修改了一些东西
fix bug
提交代码
```

---

## 6. 常见提交示例

### 新增功能

```text
feat(memory): 添加记忆更新按钮
```

```text
feat(agent): 支持二次回复流程
```

### 修复问题

```text
fix(ui): 修复进度状态不更新问题
```

```text
fix(db): 修复记忆记录插入失败问题
```

### 修改文档

```text
docs: 添加项目开发说明
```

```text
docs(memory): 补充记忆表字段说明
```

### 代码重构

```text
refactor(workflow): 拆分记忆检索流程
```

```text
refactor(agent): 简化对话处理逻辑
```

### 配置或依赖调整

```text
chore: 初始化项目配置
```

```text
chore: 更新依赖版本
```

---

## 7. 多行提交说明

如果一次提交比较复杂，可以写多行说明。

格式如下：

```text
<type>(<scope>): <subject>

<body>
```

示例：

```text
feat(memory): 添加记忆更新流程占位实现

点击记忆更新按钮后，会依次显示对话探查中、事件分析中、
记忆整理中和更新完成等状态。

当前版本仅用于 demo 展示，暂未接入真实记忆写入逻辑。
```

小项目中多数情况下只写第一行即可，只有改动较大时再补充 Body。

---

## 8. 提交建议

一次提交尽量只做一类事情。

推荐：

```text
feat(memory): 添加记忆更新按钮
fix(ui): 修复按钮样式错位
docs: 更新使用说明
```

不推荐把很多无关内容放在一次提交里：

```text
feat: 添加按钮、修 bug、改文档、更新依赖
```

这样后续查看历史或回滚代码会比较麻烦。

---

## 9. 推荐提交流程

开发时可以按照下面的方式提交：

```bash
git add .
git commit -m "feat(memory): 添加记忆更新按钮"
```

如果需要写更详细的提交说明，可以使用：

```bash
git commit
```

然后在编辑器中填写：

```text
feat(memory): 添加记忆更新流程占位实现

点击按钮后显示多个模拟进度状态，用于演示记忆更新流程。
当前版本暂不接入真实后端逻辑。
```

---

## 10. 本项目约定

本项目统一采用中文提交说明。

提交格式优先使用：

```text
<type>(<scope>): <subject>
```

其中 `scope` 可以省略。

推荐示例：

```text
feat(memory): 添加记忆更新按钮
fix(workflow): 修复检索流程状态异常
docs: 更新项目开发文档
refactor(agent): 拆分对话处理模块
chore: 调整项目目录结构
```

对于简单提交，可以使用：

```text
fix: 修复启动失败问题
docs: 补充 README
chore: 初始化项目
```

---

## 11. 总结

本项目不强制复杂的提交规则，只要求提交信息做到：

1. 看得出改动类型；
2. 看得出影响范围；
3. 看得出具体做了什么。

也就是尽量写成：

```text
feat(memory): 添加记忆更新按钮
```

而不是：

```text
update
```

