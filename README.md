# 长江水情云端采集

GitHub Actions 每小时第 7 分和第 37 分运行一次。每个站点保存为一份 CSV，
新观测追加到原文件，同一站点同一时间的数据修订会更新，完全相同的数据跳过。

## 上传方法

把本文件夹**里面的全部内容**上传到 GitHub 仓库根目录。上传后仓库根目录应直接看到：

- `yangtze_cloud.py`
- `requirements.txt`
- `.github/workflows/yangtze.yml`

不要让它们外面再套一层“长江爬虫”目录，否则 GitHub 不会识别定时配置。

上传完成后进入仓库的 Actions 页面，选择 `Yangtze water data`，点击
`Run workflow` 手动测试。首次成功后会生成 `长江数据` 文件夹。

