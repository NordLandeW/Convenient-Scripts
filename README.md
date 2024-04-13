# Convenient-Scripts

一个脚本合集，由NekoGPT（一个GPTs）和我共同制作。

请直接 Clone 该 Repo 以运行脚本。使用 Raw Download 可能会导致不可预料的编码问题。

## Scripts List

* block_programs_from_internet
  * 批量ban文件夹下所有程序的出入站连接。
  * 适用于Windows自带的防火墙。
  * 如果你觉得某个程序好用，但你又不放心，那就只能做一些基本的防护了。
* auto_decompression
  * `pip install -r requirements.txt` 以安装依赖。
  * 为了适应日渐套娃的资源分享环境而写作的自动解压嵌套文件脚本。To make your life easier.
  * 进度条 / 速度显示。
  * 读取脚本目录下的`dict.txt`作为密码字典进行自动尝试解压。
  * 若没有符合条件的密码将会对单个文件进行密码的询问。
  * 脚本使用`7z`进行解压。建议安装[7z-zstd](https://github.com/mcmilk/7-Zip-zstd)并将安装目录添加到系统环境变量下的PATH以应对任何可能的解压格式。
  * 十分的快。
  * 暂不适用于分卷文件。
