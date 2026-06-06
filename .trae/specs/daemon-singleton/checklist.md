# Checklist

- [x] 首次启动：PID 文件创建，daemon 正常运行
- [x] 重复启动：告警消息输出到终端和 daemon.log，exit code = 1
- [x] 残留 PID 清理：原进程已死后，新实例可正常启动
- [x] PID 复用检测：无关进程占用 PID 时，告警并继续启动 (implemented via WMI CommandLine check)
- [x] 正常退出：PID 文件被删除
