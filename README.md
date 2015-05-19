# OOMMonitor

> 大道至简 > If not me then who, if not now then when!


内存溢出监测程序：根据系统日志和 `.hprof` 文件来检测是否发生内存溢出。

##原理
+ 请了解 `hprof` 文件生成的原因
+ 日志关键字检测，如：`java.lang.OutOfMemoryError`, `Java heap space`.
