/**
 * 运维常用命令大全 v2 — 命令数据库
 * =================================
 * 每条命令: name / category / syntax / description / keywords / examples
 * keywords 用于双向检索：命令名搜索 + 功能关键词搜索
 */
const OPS_COMMANDS = {
  categories: [
    { id: "system",     name: "系统管理", icon: "🖥️" },
    { id: "network",    name: "网络诊断", icon: "🌐" },
    { id: "file",       name: "文件操作", icon: "📁" },
    { id: "process",    name: "进程管理", icon: "⚙️" },
    { id: "log",        name: "日志查看", icon: "📜" },
    { id: "permission", name: "权限管理", icon: "🔐" }
  ],

  commands: [
    // ==================== 系统管理 ====================
    { name:"top", category:"system", syntax:"top [-d 秒数] [-p PID] [-u 用户]", description:"实时查看系统进程、CPU、内存使用情况", keywords:["系统","进程","CPU","内存","监控","资源","实时"], examples:[
      {cmd:"top", desc:"实时显示系统进程和资源占用"},
      {cmd:"top -d 5", desc:"每5秒刷新一次"},
      {cmd:"top -p 1234", desc:"只监控PID为1234的进程"},
      {cmd:"top -u nginx", desc:"只显示nginx用户的进程"}
    ]},
    { name:"htop", category:"system", syntax:"htop [-d 延迟] [-u 用户] [-p PID]", description:"top增强版，支持鼠标操作和彩色显示", keywords:["系统","进程","CPU","内存","监控","增强","交互式"], examples:[
      {cmd:"htop", desc:"交互式进程查看器"},
      {cmd:"htop -p 1234,5678", desc:"监控多个PID"}
    ]},
    { name:"free", category:"system", syntax:"free [-h] [-m] [-g] [-s 秒数]", description:"查看内存使用情况（总量/已用/可用/缓存）", keywords:["内存","系统","查看","使用"], examples:[
      {cmd:"free -h", desc:"人类可读格式显示内存"},
      {cmd:"free -m", desc:"以MB为单位显示"},
      {cmd:"free -s 5", desc:"每5秒刷新一次"}
    ]},
    { name:"df", category:"system", syntax:"df [-h] [-T] [-i] [路径]", description:"查看磁盘空间使用情况", keywords:["磁盘","空间","系统","查看","使用"], examples:[
      {cmd:"df -h", desc:"人类可读格式显示磁盘使用"},
      {cmd:"df -Th", desc:"显示文件系统类型"},
      {cmd:"df -i", desc:"显示inode使用情况"},
      {cmd:"df -h /var", desc:"查看/var分区使用情况"}
    ]},
    { name:"du", category:"system", syntax:"du [-h] [-s] [--max-depth=N] [路径]", description:"查看目录或文件的磁盘占用大小", keywords:["磁盘","目录","大小","占用","空间"], examples:[
      {cmd:"du -sh /var/log", desc:"显示/var/log总大小"},
      {cmd:"du -h --max-depth=1 /", desc:"显示根目录下各子目录大小"},
      {cmd:"du -sh * | sort -rh | head -10", desc:"当前目录占用最大的10个文件/目录"}
    ]},
    { name:"uname", category:"system", syntax:"uname [-a] [-r] [-m] [-n]", description:"查看系统内核和操作系统信息", keywords:["系统","内核","版本","信息"], examples:[
      {cmd:"uname -a", desc:"显示所有系统信息"},
      {cmd:"uname -r", desc:"显示内核版本"},
      {cmd:"uname -m", desc:"显示系统架构"}
    ]},
    { name:"uptime", category:"system", syntax:"uptime [-p] [-s]", description:"查看系统运行时间和负载均衡", keywords:["系统","运行时间","负载","在线时长"], examples:[
      {cmd:"uptime", desc:"显示运行时间和平均负载"},
      {cmd:"uptime -p", desc:"以友好格式显示运行时间"}
    ]},
    { name:"lsblk", category:"system", syntax:"lsblk [-f] [-o 列名]", description:"列出块设备（磁盘、分区）的树形结构", keywords:["磁盘","设备","分区","块设备"], examples:[
      {cmd:"lsblk", desc:"树形列出所有块设备"},
      {cmd:"lsblk -f", desc:"显示文件系统信息"}
    ]},
    { name:"lsof", category:"system", syntax:"lsof [-i:端口] [-u 用户] [-p PID] [文件]", description:"列出打开的文件和占用进程", keywords:["文件","端口","进程","占用","打开"], examples:[
      {cmd:"lsof -i:8080", desc:"查看占用8080端口的进程"},
      {cmd:"lsof -u root", desc:"查看root用户打开的文件"},
      {cmd:"lsof /var/log/syslog", desc:"查看谁打开了syslog文件"}
    ]},
    { name:"dmesg", category:"system", syntax:"dmesg [-T] [--level=级别] | tail", description:"查看内核环缓冲区日志（硬件、驱动信息）", keywords:["内核","日志","硬件","驱动","系统"], examples:[
      {cmd:"dmesg -T | tail -20", desc:"查看最近20条内核日志（带时间戳）"},
      {cmd:"dmesg --level=err", desc:"只显示错误级别日志"}
    ]},
    { name:"sysctl", category:"system", syntax:"sysctl [-a] [-w 参数=值] 参数名", description:"查看和修改内核运行时参数", keywords:["内核","参数","系统","配置"], examples:[
      {cmd:"sysctl -a | grep vm.swappiness", desc:"查看交换分区参数"},
      {cmd:"sysctl -w net.ipv4.ip_forward=1", desc:"临时开启IP转发"}
    ]},
    { name:"vmstat", category:"system", syntax:"vmstat [间隔] [次数] [-a] [-d]", description:"查看虚拟内存、进程、CPU活动统计", keywords:["内存","CPU","系统","虚拟内存","统计"], examples:[
      {cmd:"vmstat 1 5", desc:"每秒采样一次，共5次"},
      {cmd:"vmstat -a", desc:"显示活跃/非活跃内存"}
    ]},
    { name:"iostat", category:"system", syntax:"iostat [-x] [-d] [-m] [间隔] [次数]", description:"查看CPU和磁盘I/O统计信息", keywords:["磁盘","I/O","CPU","统计","性能","IO"], examples:[
      {cmd:"iostat -x 1 5", desc:"每秒显示扩展I/O统计，共5次"},
      {cmd:"iostat -d sda 1", desc:"每秒显示sda磁盘I/O"},
      {cmd:"iostat -m", desc:"以MB为单位显示"}
    ]},
    { name:"sar", category:"system", syntax:"sar [-u] [-r] [-d] [-n DEV] [-f 文件] [间隔] [次数]", description:"系统活动报告器，采集和报告系统性能数据", keywords:["系统","性能","统计","CPU","内存","网络","历史"], examples:[
      {cmd:"sar -u 1 5", desc:"每秒采集CPU使用率，共5次"},
      {cmd:"sar -r", desc:"查看内存使用历史"},
      {cmd:"sar -n DEV 1 3", desc:"查看网络接口流量"}
    ]},
    { name:"watch", category:"system", syntax:"watch [-n 秒数] [-d] 命令", description:"周期性执行命令并全屏显示输出", keywords:["监控","实时","周期","重复","观察"], examples:[
      {cmd:"watch -n 1 'ps aux | grep nginx'", desc:"每秒查看nginx进程"},
      {cmd:"watch -d free -h", desc:"监控内存变化，高亮差异"},
      {cmd:"watch 'ls -l /tmp'", desc:"监控/tmp目录变化"}
    ]},
    { name:"alias", category:"system", syntax:"alias [别名='命令'] | unalias 别名", description:"创建命令别名，简化常用操作", keywords:["别名","简化","快捷","shell"], examples:[
      {cmd:"alias ll='ls -la'", desc:"创建ll别名"},
      {cmd:"alias", desc:"列出所有别名"},
      {cmd:"unalias ll", desc:"删除ll别名"}
    ]},
    { name:"history", category:"system", syntax:"history [-c] [-a] [-w] [数量]", description:"查看和管理Shell命令历史记录", keywords:["历史","命令","shell","记录"], examples:[
      {cmd:"history", desc:"列出所有历史命令"},
      {cmd:"history 20", desc:"查看最近20条命令"},
      {cmd:"!123", desc:"重新执行第123号历史命令"},
      {cmd:"history -c", desc:"清空历史记录"}
    ]},

    { name:"hostnamectl", category:"system", syntax:"hostnamectl [set-hostname 主机名] [status]", description:"查看和修改系统主机名及操作系统信息", keywords:["系统","主机名","信息","版本","操作系统"], examples:[
      {cmd:"hostnamectl", desc:"显示当前主机名和系统信息"},
      {cmd:"hostnamectl set-hostname web-server-01", desc:"设置新主机名"}
    ]},
    { name:"lscpu", category:"system", syntax:"lscpu [-e] [-p]", description:"查看CPU架构详细信息（核数/型号/缓存等）", keywords:["CPU","硬件","架构","信息","处理器"], examples:[
      {cmd:"lscpu", desc:"显示CPU完整信息"},
      {cmd:"lscpu | grep 'Model name'", desc:"查看CPU型号"}
    ]},
    { name:"dstat", category:"system", syntax:"dstat [-c] [-d] [-n] [-m] [间隔] [次数]", description:"综合性能监控工具，同时显示CPU/磁盘/网络/内存", keywords:["性能","监控","CPU","磁盘","网络","综合"], examples:[
      {cmd:"dstat 1 10", desc:"每秒刷新综合指标，共10次"},
      {cmd:"dstat -c -d -n -m 1", desc:"同时监控CPU/磁盘/网络/内存"}
    ]},
    { name:"mpstat", category:"system", syntax:"mpstat [-P ALL] [间隔] [次数]", description:"查看每个CPU核心的使用率", keywords:["CPU","核心","性能","多核","统计"], examples:[
      {cmd:"mpstat -P ALL 1 5", desc:"每秒显示所有核心使用率，共5次"},
      {cmd:"mpstat 1", desc:"每秒显示CPU汇总"}
    ]},
    { name:"pidstat", category:"system", syntax:"pidstat [-u] [-r] [-d] [-p PID] [间隔] [次数]", description:"查看指定进程的CPU/内存/磁盘I/O统计", keywords:["进程","性能","CPU","内存","I/O","统计"], examples:[
      {cmd:"pidstat -u 1 5", desc:"每秒显示进程CPU使用率"},
      {cmd:"pidstat -d -p 1234 1", desc:"监控PID 1234的磁盘I/O"}
    ]},
    { name:"timedatectl", category:"system", syntax:"timedatectl [set-timezone 时区] [set-ntp true/false]", description:"查看和设置系统日期、时间、时区", keywords:["时间","日期","时区","NTP","设置"], examples:[
      {cmd:"timedatectl", desc:"显示当前时间和时区设置"},
      {cmd:"timedatectl set-timezone Asia/Shanghai", desc:"设置时区为上海"},
      {cmd:"timedatectl set-ntp true", desc:"开启NTP自动同步"}
    ]},
    { name:"date", category:"system", syntax:"date [-d 字符串] [+格式] [-s 日期时间]", description:"查看和设置系统日期时间", keywords:["时间","日期","显示","设置"], examples:[
      {cmd:"date", desc:"显示当前日期时间"},
      {cmd:"date +'%Y-%m-%d %H:%M:%S'", desc:"按格式显示"},
      {cmd:"date -d '1 hour ago'", desc:"显示1小时前的时间"}
    ]},
    { name:"dmidecode", category:"system", syntax:"dmidecode [-t 类型] [-s 关键字]", description:"查看硬件信息（BIOS/内存/主板等）", keywords:["硬件","BIOS","内存","信息","型号"], examples:[
      {cmd:"dmidecode -t memory", desc:"查看内存插槽和型号信息"},
      {cmd:"dmidecode -s system-serial-number", desc:"查看系统序列号"}
    ]},
    { name:"sleep", category:"system", syntax:"sleep 秒数[s/m/h/d]", description:"暂停指定时间后继续执行", keywords:["等待","延迟","暂停","定时"], examples:[
      {cmd:"sleep 5 && echo done", desc:"等待5秒后输出done"},
      {cmd:"sleep 1m", desc:"等待1分钟"}
    ]},
    { name:"echo", category:"system", syntax:"echo [-n] [-e] 文本", description:"输出文本到标准输出", keywords:["输出","显示","打印","文本"], examples:[
      {cmd:"echo 'Hello World'", desc:"输出文本"},
      {cmd:"echo -e 'Line1\\nLine2'", desc:"输出多行文本"},
      {cmd:"echo $PATH", desc:"输出环境变量值"}
    ]},

    // ==================== 网络诊断 ====================
    { name:"ping", category:"network", syntax:"ping [-c 次数] [-i 间隔] [-W 超时] 主机", description:"测试网络连通性和延迟", keywords:["网络","连通","延迟","测试","ping","ICMP"], examples:[
      {cmd:"ping -c 4 8.8.8.8", desc:"ping 4次后停止"},
      {cmd:"ping -i 0.5 192.168.1.1", desc:"每0.5秒ping一次"},
      {cmd:"ping -W 2 example.com", desc:"设置2秒超时"}
    ]},
    { name:"netstat", category:"network", syntax:"netstat [-t] [-u] [-l] [-n] [-p] [-a]", description:"查看网络连接、端口监听、路由表", keywords:["网络","端口","连接","监听","查看端口","查看连接"], examples:[
      {cmd:"netstat -tlnp", desc:"查看所有TCP监听端口和进程"},
      {cmd:"netstat -an | grep ESTABLISHED", desc:"查看已建立的连接"},
      {cmd:"netstat -rn", desc:"查看路由表"}
    ]},
    { name:"ss", category:"network", syntax:"ss [-t] [-u] [-l] [-n] [-p] [-a] [-m]", description:"netstat的现代替代品，速度更快", keywords:["网络","端口","连接","监听","查看端口","socket"], examples:[
      {cmd:"ss -tlnp", desc:"查看TCP监听端口和进程"},
      {cmd:"ss -s", desc:"显示socket统计摘要"},
      {cmd:"ss -t state established", desc:"查看已建立的TCP连接"}
    ]},
    { name:"tcpdump", category:"network", syntax:"tcpdump [-i 接口] [-c 数量] [-w 文件] [过滤表达式]", description:"抓取和分析网络数据包", keywords:["网络","抓包","数据包","分析","抓取流量"], examples:[
      {cmd:"tcpdump -i eth0 port 80", desc:"抓取eth0上80端口流量"},
      {cmd:"tcpdump -c 100 -w capture.pcap", desc:"抓100个包存到文件"},
      {cmd:"tcpdump host 192.168.1.100", desc:"抓取指定主机的流量"}
    ]},
    { name:"curl", category:"network", syntax:"curl [-X 方法] [-H 头] [-d 数据] [-o 文件] [-I] [-w 格式] URL", description:"发送HTTP请求，测试API和网页", keywords:["网络","HTTP","请求","API","测试","下载","curl"], examples:[
      {cmd:"curl -I http://example.com", desc:"只获取HTTP头"},
      {cmd:"curl -X POST -H 'Content-Type: application/json' -d '{\"key\":\"value\"}' http://api.example.com", desc:"发送POST JSON请求"},
      {cmd:"curl -o file.zip http://example.com/file.zip", desc:"下载文件"},
      {cmd:"curl -w '%{http_code} %{time_total}s\\n' -o /dev/null -s http://example.com", desc:"只输出状态码和耗时"}
    ]},
    { name:"wget", category:"network", syntax:"wget [-O 文件] [-c] [-r] [--limit-rate=速度] URL", description:"下载文件，支持断点续传和递归下载", keywords:["网络","下载","文件","断点续传"], examples:[
      {cmd:"wget http://example.com/file.tar.gz", desc:"下载文件"},
      {cmd:"wget -c http://example.com/largefile.iso", desc:"断点续传"},
      {cmd:"wget -O custom.zip http://example.com/file.zip", desc:"指定保存文件名"}
    ]},
    { name:"dig", category:"network", syntax:"dig [@DNS服务器] 域名 [记录类型] [+short]", description:"DNS域名解析查询（替代nslookup）", keywords:["网络","DNS","域名","解析","查询IP"], examples:[
      {cmd:"dig example.com", desc:"查询域名A记录"},
      {cmd:"dig @8.8.8.8 example.com MX", desc:"指定DNS服务器查MX记录"},
      {cmd:"dig example.com +short", desc:"只输出解析结果IP"}
    ]},
    { name:"nslookup", category:"network", syntax:"nslookup 域名 [DNS服务器]", description:"DNS域名解析查询（交互式）", keywords:["网络","DNS","域名","解析"], examples:[
      {cmd:"nslookup example.com", desc:"查询域名解析"},
      {cmd:"nslookup example.com 8.8.8.8", desc:"指定DNS服务器查询"}
    ]},
    { name:"traceroute", category:"network", syntax:"traceroute [-n] [-w 超时] [-q 次数] 主机", description:"追踪数据包到目标主机的路由路径", keywords:["网络","路由","追踪","路径","跳数"], examples:[
      {cmd:"traceroute 8.8.8.8", desc:"追踪到8.8.8.8的路由"},
      {cmd:"traceroute -n example.com", desc:"不解析主机名，加快速度"}
    ]},
    { name:"nc", category:"network", syntax:"nc [-z] [-v] [-l] [-p 端口] 主机 端口", description:"网络工具瑞士军刀，端口扫描/监听/传输", keywords:["网络","端口","扫描","监听","nc","netcat","瑞士军刀"], examples:[
      {cmd:"nc -zv 192.168.1.1 80", desc:"检测80端口是否开放"},
      {cmd:"nc -zv 192.168.1.1 20-80", desc:"扫描20-80端口范围"},
      {cmd:"nc -l 8080 > received.txt", desc:"监听8080端口接收文件"}
    ]},
    { name:"ip", category:"network", syntax:"ip [addr|link|route|neigh] [show|add|del] ...", description:"查看和配置网络接口、路由（替代ifconfig）", keywords:["网络","网卡","IP","路由","接口","地址"], examples:[
      {cmd:"ip addr show", desc:"显示所有网络接口"},
      {cmd:"ip route show", desc:"查看路由表"},
      {cmd:"ip link set eth0 up", desc:"启用eth0网卡"}
    ]},
    { name:"ifconfig", category:"network", syntax:"ifconfig [接口] [up|down] [IP]", description:"查看和配置网络接口（旧版工具）", keywords:["网络","网卡","IP","接口","地址"], examples:[
      {cmd:"ifconfig", desc:"显示所有网络接口"},
      {cmd:"ifconfig eth0", desc:"查看eth0接口信息"},
      {cmd:"ifconfig eth0 192.168.1.100 netmask 255.255.255.0", desc:"设置IP地址"}
    ]},
    { name:"arp", category:"network", syntax:"arp [-a] [-n] [-d] [-s]", description:"查看和管理ARP缓存表（IP-MAC映射）", keywords:["网络","ARP","MAC","缓存","地址"], examples:[
      {cmd:"arp -a", desc:"显示所有ARP缓存"},
      {cmd:"arp -n", desc:"以数字格式显示"}
    ]},

    { name:"nmap", category:"network", syntax:"nmap [-sS] [-sV] [-p 端口] 目标", description:"端口扫描和服务版本探测", keywords:["网络","扫描","端口","安全","探测","服务"], examples:[
      {cmd:"nmap -sV 192.168.1.1", desc:"扫描目标开放端口和服务版本"},
      {cmd:"nmap -p 1-1000 192.168.1.1", desc:"扫描1-1000端口范围"},
      {cmd:"nmap -sS -Pn target.com", desc:"隐蔽SYN扫描"}
    ]},
    { name:"mtr", category:"network", syntax:"mtr [-r] [-c 次数] [-n] 主机", description:"实时网络诊断工具，结合ping+traceroute", keywords:["网络","路由","延迟","丢包","诊断","实时"], examples:[
      {cmd:"mtr 8.8.8.8", desc:"实时显示到8.8.8.8的路由和丢包"},
      {cmd:"mtr -r -c 10 example.com", desc:"发送10个包后输出报告"}
    ]},
    { name:"host", category:"network", syntax:"host [-t 类型] 域名 [DNS服务器]", description:"简单的DNS查询工具", keywords:["网络","DNS","域名","解析","查询"], examples:[
      {cmd:"host example.com", desc:"查询域名A记录"},
      {cmd:"host -t MX example.com", desc:"查询邮件交换记录"},
      {cmd:"host example.com 8.8.8.8", desc:"指定DNS服务器查询"}
    ]},
    { name:"ssh-keygen", category:"network", syntax:"ssh-keygen [-t 类型] [-b 位数] [-f 文件] [-C 注释]", description:"生成SSH密钥对", keywords:["SSH","密钥","生成","安全","认证"], examples:[
      {cmd:"ssh-keygen -t rsa -b 4096 -C 'email@example.com'", desc:"生成4096位RSA密钥"},
      {cmd:"ssh-keygen -t ed25519 -f ~/.ssh/id_custom", desc:"生成Ed25519密钥到指定路径"}
    ]},
    { name:"sftp", category:"network", syntax:"sftp [-P 端口] user@host", description:"SSH安全文件传输（交互式）", keywords:["SSH","文件","传输","安全","FTP"], examples:[
      {cmd:"sftp user@192.168.1.1", desc:"连接远程主机进入交互模式"},
      {cmd:"sftp -P 2222 user@host", desc:"指定端口连接"}
    ]},
    { name:"iptables", category:"network", syntax:"iptables [-L] [-A 链] [-D 链] [-I 链] [-P 链 策略] 规则", description:"Linux内核防火墙规则管理", keywords:["防火墙","安全","规则","网络","过滤","NAT"], examples:[
      {cmd:"iptables -L -n -v", desc:"查看所有防火墙规则"},
      {cmd:"iptables -A INPUT -p tcp --dport 80 -j ACCEPT", desc:"允许TCP 80端口入站"},
      {cmd:"iptables -I INPUT -s 192.168.1.0/24 -j DROP", desc:"禁止整个网段访问"}
    ]},
    { name:"route", category:"network", syntax:"route [-n] [add|del] [-net|-host] 目标 [netmask 掩码] [gw 网关]", description:"查看和修改内核路由表", keywords:["网络","路由","网关","路由表"], examples:[
      {cmd:"route -n", desc:"查看路由表"},
      {cmd:"route add -net 10.0.0.0/8 gw 192.168.1.1", desc:"添加静态路由"},
      {cmd:"route del default gw 192.168.1.1", desc:"删除默认网关"}
    ]},
    { name:"iftop", category:"network", syntax:"iftop [-i 接口] [-n] [-P] [-B]", description:"实时查看网络接口流量（按连接展示）", keywords:["网络","流量","带宽","实时","监控","网卡"], examples:[
      {cmd:"iftop -i eth0", desc:"监控eth0接口流量"},
      {cmd:"iftop -n -P", desc:"不解析主机名，显示端口号"}
    ]},

    // ==================== 文件操作 ====================
    { name:"ls", category:"file", syntax:"ls [-l] [-a] [-h] [-t] [-r] [-R] [路径]", description:"列出目录内容", keywords:["文件","目录","列表","查看","列出","显示"], examples:[
      {cmd:"ls -la", desc:"显示所有文件（含隐藏文件）的详细信息"},
      {cmd:"ls -lhrt", desc:"按修改时间逆序显示（最新的在最后）"},
      {cmd:"ls -R /var", desc:"递归列出/var下所有内容"}
    ]},
    { name:"find", category:"file", syntax:"find 路径 [-name 名称] [-type 类型] [-size 大小] [-mtime 天数] [-exec 命令 {} \\;]", description:"按条件搜索文件和目录", keywords:["文件","搜索","查找","查找文件","搜索文件"], examples:[
      {cmd:"find /var/log -name '*.log'", desc:"查找所有.log文件"},
      {cmd:"find / -type f -size +100M", desc:"查找大于100MB的文件"},
      {cmd:"find /tmp -mtime -1", desc:"查找24小时内修改过的文件"},
      {cmd:"find . -name '*.py' -exec wc -l {} \\;", desc:"统计所有Python文件行数"}
    ]},
    { name:"grep", category:"file", syntax:"grep [-r] [-i] [-n] [-v] [-c] [-A行数] [-B行数] '模式' 文件", description:"在文件中搜索匹配文本（正则表达式）", keywords:["文件","搜索","文本","正则","匹配","查找文本","过滤"], examples:[
      {cmd:"grep -rn 'error' /var/log/", desc:"递归搜索日志中的error"},
      {cmd:"grep -i 'warning' app.log", desc:"忽略大小写搜索warning"},
      {cmd:"grep -v '#' nginx.conf", desc:"排除注释行"},
      {cmd:"grep -A 3 -B 1 'Exception' app.log", desc:"显示匹配行前后内容"}
    ]},
    { name:"cat", category:"file", syntax:"cat [-n] [-s] [文件...]", description:"连接文件并打印到标准输出", keywords:["文件","查看","输出","连接","显示内容"], examples:[
      {cmd:"cat file.txt", desc:"查看文件内容"},
      {cmd:"cat -n file.txt", desc:"带行号显示"},
      {cmd:"cat file1.txt file2.txt > merged.txt", desc:"合并多个文件"}
    ]},
    { name:"tar", category:"file", syntax:"tar [-c] [-x] [-z] [-j] [-v] [-f 文件] [-C 目录] [文件...]", description:"打包和解压归档文件（支持gzip/bzip2）", keywords:["文件","压缩","打包","解压","归档","tar","压缩文件"], examples:[
      {cmd:"tar -czvf archive.tar.gz /path/dir", desc:"打包并用gzip压缩"},
      {cmd:"tar -xzvf archive.tar.gz", desc:"解压.tar.gz文件"},
      {cmd:"tar -xjvf archive.tar.bz2", desc:"解压.tar.bz2文件"},
      {cmd:"tar -tf archive.tar.gz", desc:"列出压缩包内容不解压"}
    ]},
    { name:"zip", category:"file", syntax:"zip [-r] 压缩包 文件...", description:"压缩文件为zip格式", keywords:["文件","压缩","打包","zip"], examples:[
      {cmd:"zip -r backup.zip /var/www", desc:"递归压缩目录"},
      {cmd:"zip backup.zip file1.txt file2.txt", desc:"压缩多个文件"}
    ]},
    { name:"unzip", category:"file", syntax:"unzip [-d 目录] [-l] 压缩包", description:"解压zip格式文件", keywords:["文件","解压","zip","解压缩"], examples:[
      {cmd:"unzip backup.zip -d /tmp/restore", desc:"解压到指定目录"},
      {cmd:"unzip -l backup.zip", desc:"查看zip内容不解压"}
    ]},
    { name:"cp", category:"file", syntax:"cp [-r] [-p] [-i] [-v] 源 目标", description:"复制文件或目录", keywords:["文件","复制","拷贝"], examples:[
      {cmd:"cp -r /source/dir /dest/dir", desc:"递归复制目录"},
      {cmd:"cp -p file.txt backup.txt", desc:"保留权限和时间戳复制"},
      {cmd:"cp -iv file.txt /tmp/", desc:"覆盖前提示并显示过程"}
    ]},
    { name:"mv", category:"file", syntax:"mv [-i] [-v] [-f] 源 目标", description:"移动或重命名文件/目录", keywords:["文件","移动","重命名"], examples:[
      {cmd:"mv old_name.txt new_name.txt", desc:"重命名文件"},
      {cmd:"mv -v /tmp/file.txt /var/log/", desc:"移动文件并显示过程"}
    ]},
    { name:"rm", category:"file", syntax:"rm [-r] [-f] [-i] [-v] 文件/目录", description:"删除文件或目录", keywords:["文件","删除","移除","删除文件"], examples:[
      {cmd:"rm -rf /tmp/test", desc:"强制递归删除目录（危险！慎用）"},
      {cmd:"rm -i *.log", desc:"删除前逐个确认"}
    ]},
    { name:"scp", category:"file", syntax:"scp [-P 端口] [-r] [-C] 源 目标", description:"通过SSH在主机间安全复制文件", keywords:["文件","复制","传输","SSH","远程","远程复制"], examples:[
      {cmd:"scp file.txt user@remote:/path/", desc:"上传本地文件到远程"},
      {cmd:"scp -r user@remote:/var/log /local/", desc:"从远程下载整个目录"},
      {cmd:"scp -P 2222 file.txt user@host:/tmp/", desc:"指定SSH端口"}
    ]},
    { name:"rsync", category:"file", syntax:"rsync [-avz] [-e 'ssh -p 端口'] [--delete] 源 目标", description:"高效远程文件同步（增量传输）", keywords:["文件","同步","备份","传输","增量","远程同步"], examples:[
      {cmd:"rsync -avz /local/dir/ user@remote:/backup/", desc:"增量同步到远程（带压缩）"},
      {cmd:"rsync -avz --delete /src/ /dest/", desc:"同步并删除目标端多余文件"},
      {cmd:"rsync -avz -e 'ssh -p 2222' /src/ user@host:/dest/", desc:"指定SSH端口"}
    ]},
    { name:"ln", category:"file", syntax:"ln [-s] 源 链接名", description:"创建硬链接或符号链接", keywords:["文件","链接","软链接","硬链接","符号链接"], examples:[
      {cmd:"ln -s /var/www/html /home/user/web", desc:"创建符号链接"},
      {cmd:"ln file.txt file_hardlink.txt", desc:"创建硬链接"}
    ]},
    { name:"wc", category:"file", syntax:"wc [-l] [-w] [-c] [文件]", description:"统计文件行数、单词数、字节数", keywords:["文件","统计","行数","字数","计数"], examples:[
      {cmd:"wc -l access.log", desc:"统计文件行数"},
      {cmd:"wc -l *.py", desc:"统计所有Python文件行数"}
    ]},
    { name:"diff", category:"file", syntax:"diff [-u] [-r] [-i] 文件1 文件2", description:"比较两个文件或目录的差异", keywords:["文件","比较","差异","对比","diff"], examples:[
      {cmd:"diff -u old.conf new.conf", desc:"以unified格式显示差异"},
      {cmd:"diff -r dir1/ dir2/", desc:"递归比较两个目录"}
    ]},
    { name:"mount", category:"file", syntax:"mount [-t 类型] [-o 选项] 设备 挂载点 | umount 挂载点", description:"挂载和卸载文件系统", keywords:["文件","挂载","文件系统","磁盘","挂载磁盘"], examples:[
      {cmd:"mount /dev/sdb1 /mnt/data", desc:"挂载sdb1到/mnt/data"},
      {cmd:"mount -t nfs 192.168.1.1:/share /mnt/nfs", desc:"挂载NFS远程目录"},
      {cmd:"umount /mnt/data", desc:"卸载/mnt/data"}
    ]},
    { name:"mkdir", category:"file", syntax:"mkdir [-p] [-m 权限] 目录", description:"创建新目录", keywords:["文件","目录","创建","新建"], examples:[
      {cmd:"mkdir -p /data/backup/2024", desc:"递归创建多级目录"},
      {cmd:"mkdir -m 755 /shared", desc:"创建时指定权限"}
    ]},
    { name:"touch", category:"file", syntax:"touch [-a] [-m] [-t 时间] 文件", description:"创建空文件或更新文件时间戳", keywords:["文件","创建","时间戳","新建文件"], examples:[
      {cmd:"touch newfile.txt", desc:"创建空文件"},
      {cmd:"touch -t 202401010000 file.txt", desc:"设置文件时间戳"}
    ]},
    { name:"which", category:"file", syntax:"which [-a] 命令名", description:"查找命令的完整路径", keywords:["文件","查找","路径","命令位置","which"], examples:[
      {cmd:"which python", desc:"显示python命令的路径"},
      {cmd:"which -a python", desc:"显示所有匹配的路径"}
    ]},
    { name:"tee", category:"file", syntax:"命令 | tee [-a] 文件", description:"将标准输入同时输出到终端和文件", keywords:["文件","输出","保存","分流","tee"], examples:[
      {cmd:"./script.sh | tee output.log", desc:"运行脚本同时保存输出"},
      {cmd:"make 2>&1 | tee -a build.log", desc:"追加模式保存编译日志"}
    ]},

    { name:"locate", category:"file", syntax:"locate [-i] [-r] 关键词", description:"从数据库中快速定位文件（比find更快）", keywords:["文件","搜索","查找","快速","定位"], examples:[
      {cmd:"locate nginx.conf", desc:"快速查找nginx.conf文件位置"},
      {cmd:"locate -i '*.log'", desc:"忽略大小写搜索log文件"},
      {cmd:"sudo updatedb", desc:"更新locate数据库（文件变化后执行）"}
    ]},
    { name:"tree", category:"file", syntax:"tree [-L 层级] [-d] [-a] [路径]", description:"以树形结构显示目录内容", keywords:["文件","目录","树形","结构","展示"], examples:[
      {cmd:"tree -L 2 /etc/nginx", desc:"显示nginx目录2层树形结构"},
      {cmd:"tree -d /var", desc:"只显示目录不显示文件"}
    ]},
    { name:"stat", category:"file", syntax:"stat [-c 格式] 文件/目录", description:"显示文件或目录的详细属性（大小/权限/时间戳/inode等）", keywords:["文件","属性","inode","时间","权限","详细信息"], examples:[
      {cmd:"stat /var/log/syslog", desc:"查看文件完整属性"},
      {cmd:"stat -c '%a %n' file.txt", desc:"以八进制显示文件权限"}
    ]},
    { name:"file", category:"file", syntax:"file [-b] [-i] 文件", description:"识别文件类型（文本/二进制/图片/压缩包等）", keywords:["文件","类型","识别","MIME"], examples:[
      {cmd:"file unknown.bin", desc:"识别文件类型"},
      {cmd:"file -i *.png", desc:"显示MIME类型"},
      {cmd:"file /dev/sda", desc:"查看设备文件类型"}
    ]},
    { name:"dd", category:"file", syntax:"dd if=输入 of=输出 [bs=块大小] [count=数量] [status=progress]", description:"复制和转换文件（常用于制作启动盘/备份/测试磁盘速度）", keywords:["文件","复制","磁盘","备份","镜像","转换"], examples:[
      {cmd:"dd if=/dev/sda of=/backup/disk.img bs=4M status=progress", desc:"制作磁盘镜像"},
      {cmd:"dd if=/dev/zero of=test.dat bs=1M count=100", desc:"生成100MB测试文件"},
      {cmd:"dd if=/dev/zero of=/dev/null bs=1M count=1000", desc:"测试磁盘写入速度"}
    ]},
    { name:"gzip", category:"file", syntax:"gzip [-k] [-d] [-v] 文件", description:"GNU zip压缩工具", keywords:["文件","压缩","gzip","打包"], examples:[
      {cmd:"gzip large.log", desc:"压缩文件为.gz格式"},
      {cmd:"gzip -k file.txt", desc:"压缩但保留原文件"},
      {cmd:"gzip -d file.gz", desc:"解压.gz文件"}
    ]},
    { name:"gunzip", category:"file", syntax:"gunzip [-k] [-v] 文件.gz", description:"解压.gz格式文件", keywords:["文件","解压","gzip"], examples:[
      {cmd:"gunzip file.gz", desc:"解压文件"},
      {cmd:"gunzip -k file.gz", desc:"解压但保留压缩包"}
    ]},
    { name:"bzip2", category:"file", syntax:"bzip2 [-k] [-d] [-v] 文件", description:"高压缩率压缩工具（压缩比比gzip更高）", keywords:["文件","压缩","打包","高压缩"], examples:[
      {cmd:"bzip2 large.log", desc:"压缩为.bz2格式"},
      {cmd:"bzip2 -d file.bz2", desc:"解压.bz2文件"}
    ]},
    { name:"split", category:"file", syntax:"split [-b 大小] [-l 行数] 文件 [前缀]", description:"将大文件分割为多个小文件", keywords:["文件","分割","拆分","大文件"], examples:[
      {cmd:"split -b 100M large.tar small_", desc:"按100MB分割大文件"},
      {cmd:"split -l 10000 access.log part_", desc:"按1万行分割日志"}
    ]},
    { name:"shred", category:"file", syntax:"shred [-f] [-n 次数] [-z] 文件", description:"安全删除文件（多次覆写，无法恢复）", keywords:["文件","删除","安全","覆写","销毁"], examples:[
      {cmd:"shred -n 3 -z secret.txt", desc:"覆写3次后清零删除"},
      {cmd:"shred -f -n 5 /dev/sdb", desc:"安全擦除整个磁盘"}
    ]},
    { name:"dirname", category:"file", syntax:"dirname 路径", description:"提取路径中的目录部分", keywords:["文件","路径","目录","提取"], examples:[
      {cmd:"dirname /var/log/nginx/access.log", desc:"输出/var/log/nginx"},
      {cmd:"dirname /home/user/file.txt", desc:"输出/home/user"}
    ]},

    // ==================== 进程管理 ====================
    { name:"ps", category:"process", syntax:"ps [aux] [-ef] [-C 命令名] [-p PID]", description:"查看当前系统进程快照", keywords:["进程","查看","列表","快照","查看进程"], examples:[
      {cmd:"ps aux", desc:"显示所有进程详细信息"},
      {cmd:"ps -ef | grep nginx", desc:"查找nginx进程"},
      {cmd:"ps -p 1234 -o pid,cmd,%cpu,%mem", desc:"查看指定PID的资源占用"}
    ]},
    { name:"kill", category:"process", syntax:"kill [-9] [-15] [-l] PID", description:"向进程发送信号（终止、强制终止等）", keywords:["进程","终止","杀死","信号","结束进程"], examples:[
      {cmd:"kill 1234", desc:"优雅终止进程（SIGTERM）"},
      {cmd:"kill -9 1234", desc:"强制终止进程（SIGKILL）"},
      {cmd:"kill -l", desc:"列出所有信号名称"}
    ]},
    { name:"pkill", category:"process", syntax:"pkill [-9] [-f] [-u 用户] 进程名/模式", description:"按名称或模式批量终止进程", keywords:["进程","终止","批量","按名称杀进程"], examples:[
      {cmd:"pkill nginx", desc:"终止所有nginx进程"},
      {cmd:"pkill -f 'python app.py'", desc:"按完整命令行匹配终止"},
      {cmd:"pkill -9 -u testuser", desc:"强制终止testuser的所有进程"}
    ]},
    { name:"killall", category:"process", syntax:"killall [-9] [-u 用户] [-r 正则] 进程名", description:"按进程名终止所有匹配进程", keywords:["进程","终止","批量"], examples:[
      {cmd:"killall httpd", desc:"终止所有httpd进程"},
      {cmd:"killall -9 nginx", desc:"强制终止所有nginx进程"}
    ]},
    { name:"pgrep", category:"process", syntax:"pgrep [-l] [-f] [-u 用户] 进程名/模式", description:"按名称查找进程PID", keywords:["进程","查找","PID","查找进程"], examples:[
      {cmd:"pgrep -l nginx", desc:"查找nginx进程的PID和名称"},
      {cmd:"pgrep -f 'python app'", desc:"按完整命令行匹配查找"}
    ]},
    { name:"nohup", category:"process", syntax:"nohup 命令 [参数] &", description:"使进程在后台运行，不受终端关闭影响", keywords:["进程","后台","守护","后台运行"], examples:[
      {cmd:"nohup python app.py > app.log 2>&1 &", desc:"后台运行并重定向输出"},
      {cmd:"nohup ./start.sh &", desc:"后台运行脚本"}
    ]},
    { name:"jobs", category:"process", syntax:"jobs [-l] [-p]", description:"查看当前Shell的后台任务列表", keywords:["进程","后台","任务","查看后台"], examples:[
      {cmd:"jobs -l", desc:"列出所有后台任务及PID"},
      {cmd:"jobs -p", desc:"只显示PID"}
    ]},
    { name:"bg", category:"process", syntax:"bg [%任务号]", description:"将挂起的任务放到后台继续运行", keywords:["进程","后台","恢复","bg"], examples:[
      {cmd:"bg %1", desc:"将任务1放到后台运行"},
      {cmd:"bg", desc:"将最近挂起的任务放后台"}
    ]},
    { name:"fg", category:"process", syntax:"fg [%任务号]", description:"将后台任务调到前台运行", keywords:["进程","前台","恢复","fg"], examples:[
      {cmd:"fg %2", desc:"将任务2调到前台"},
      {cmd:"fg", desc:"将最近的后台任务调到前台"}
    ]},
    { name:"nice", category:"process", syntax:"nice -n 优先级 命令", description:"以指定优先级启动进程（-20~19，越低越高）", keywords:["进程","优先级","启动","nice"], examples:[
      {cmd:"nice -n 10 tar -czvf backup.tar.gz /data", desc:"以低优先级运行备份"},
      {cmd:"nice -n -5 ./important_task", desc:"以较高优先级运行任务"}
    ]},
    { name:"renice", category:"process", syntax:"renice 优先级 -p PID [-u 用户] [-g 组]", description:"调整运行中进程的优先级", keywords:["进程","优先级","调整","renice"], examples:[
      {cmd:"renice -5 -p 1234", desc:"提高PID 1234的优先级"},
      {cmd:"renice 10 -u nginx", desc:"降低nginx用户所有进程的优先级"}
    ]},
    { name:"systemctl", category:"process", syntax:"systemctl [start|stop|restart|status|enable|disable] 服务名", description:"管理systemd服务（启动/停止/开机自启）", keywords:["服务","进程","启动","停止","systemd","systemctl","管理服务"], examples:[
      {cmd:"systemctl status nginx", desc:"查看nginx服务状态"},
      {cmd:"systemctl restart nginx", desc:"重启nginx服务"},
      {cmd:"systemctl enable nginx", desc:"设置nginx开机自启"},
      {cmd:"systemctl list-units --type=service --state=running", desc:"列出所有运行中的服务"}
    ]},
    { name:"service", category:"process", syntax:"service 服务名 [start|stop|restart|status]", description:"管理SysV服务（旧版服务管理）", keywords:["服务","进程","启动","停止"], examples:[
      {cmd:"service nginx status", desc:"查看nginx状态"},
      {cmd:"service networking restart", desc:"重启网络服务"}
    ]},
    { name:"crontab", category:"process", syntax:"crontab [-l] [-e] [-r] [-u 用户]", description:"管理定时任务计划", keywords:["定时","任务","计划","定时任务","cron","调度"], examples:[
      {cmd:"crontab -l", desc:"查看当前用户的定时任务"},
      {cmd:"crontab -e", desc:"编辑定时任务"},
      {cmd:"0 2 * * * /backup.sh", desc:"每天凌晨2点执行备份（cron表达式示例）"}
    ]},
    { name:"strace", category:"process", syntax:"strace [-f] [-p PID] [-e 系统调用] [-c] 命令", description:"追踪进程的系统调用和信号", keywords:["进程","调试","追踪","系统调用","排查","strace"], examples:[
      {cmd:"strace -p 1234", desc:"追踪运行中进程的系统调用"},
      {cmd:"strace -e open nginx -t", desc:"追踪nginx打开的文件"},
      {cmd:"strace -c ls", desc:"统计ls命令的系统调用耗时"}
    ]},

    { name:"pstree", category:"process", syntax:"pstree [-p] [-a] [-u 用户] [PID]", description:"以树形结构显示进程父子关系", keywords:["进程","树形","父子","关系","结构"], examples:[
      {cmd:"pstree -p", desc:"树形显示进程并标注PID"},
      {cmd:"pstree -a nginx", desc:"显示nginx进程树及完整命令行"}
    ]},
    { name:"pidof", category:"process", syntax:"pidof [-s] 进程名", description:"按进程名获取PID", keywords:["进程","PID","查找","获取"], examples:[
      {cmd:"pidof nginx", desc:"获取nginx所有进程PID"},
      {cmd:"pidof -s sshd", desc:"只返回一个PID"}
    ]},
    { name:"fuser", category:"process", syntax:"fuser [-v] [-k] [-m] 文件/端口", description:"查看哪些进程正在使用指定文件或端口", keywords:["进程","文件","端口","占用","查找"], examples:[
      {cmd:"fuser -v 80/tcp", desc:"查看占用80端口的进程"},
      {cmd:"fuser -m /data", desc:"查看哪些进程正在使用/data目录"},
      {cmd:"fuser -k /var/log/app.log", desc:"终止使用该日志文件的进程"}
    ]},
    { name:"screen", category:"process", syntax:"screen [-S 名称] [-ls] [-r 会话]", description:"终端多路复用器，保持会话不随SSH断开而终止", keywords:["终端","会话","后台","保持","多窗口"], examples:[
      {cmd:"screen -S myapp", desc:"创建名为myapp的会话"},
      {cmd:"screen -ls", desc:"列出所有会话"},
      {cmd:"screen -r myapp", desc:"重新连接到myapp会话"},
      {cmd:"Ctrl+A D", desc:"从screen会话中分离（保持运行）"}
    ]},
    { name:"at", category:"process", syntax:"at [-f 脚本] 时间", description:"安排一次性定时任务", keywords:["定时","任务","一次性","调度"], examples:[
      {cmd:"at now + 1 hour", desc:"1小时后执行（交互输入命令后Ctrl+D提交）"},
      {cmd:"at -f /backup.sh 2:30 AM tomorrow", desc:"明天凌晨2:30执行脚本"},
      {cmd:"atq", desc:"查看待执行的at任务队列"}
    ]},
    { name:"disown", category:"process", syntax:"disown [-h] [-a] [%任务号]", description:"将后台任务从Shell作业列表中移除，终端关闭也不终止", keywords:["进程","后台","分离","守护"], examples:[
      {cmd:"disown %1", desc:"将任务1从Shell中分离"},
      {cmd:"disown -a", desc:"分离所有后台任务"}
    ]},

    // ==================== 日志查看 ====================
    { name:"tail", category:"log", syntax:"tail [-n 行数] [-f] [-F] [--pid=PID] 文件", description:"查看文件末尾内容，支持实时跟踪", keywords:["日志","查看","末尾","实时","跟踪","实时日志","查看日志"], examples:[
      {cmd:"tail -f /var/log/syslog", desc:"实时跟踪日志输出"},
      {cmd:"tail -n 100 access.log", desc:"查看最后100行"},
      {cmd:"tail -F /var/log/nginx/error.log", desc:"文件轮转后也继续跟踪"}
    ]},
    { name:"head", category:"log", syntax:"head [-n 行数] [-c 字节数] 文件", description:"查看文件开头内容", keywords:["日志","查看","开头"], examples:[
      {cmd:"head -n 20 /var/log/syslog", desc:"查看前20行"},
      {cmd:"head -c 1024 data.bin", desc:"查看前1024字节"}
    ]},
    { name:"less", category:"log", syntax:"less [+行号] [-N] [-I] 文件", description:"分页查看大文件（支持搜索/前后翻页）", keywords:["日志","查看","分页","浏览","查看大文件"], examples:[
      {cmd:"less -N /var/log/syslog", desc:"带行号分页查看"},
      {cmd:"less +F /var/log/syslog", desc:"类似tail -f的实时跟踪模式"},
      {cmd:"less +/error /var/log/syslog", desc:"打开后自动跳到第一个error处"}
    ]},
    { name:"journalctl", category:"log", syntax:"journalctl [-u 服务] [--since] [--until] [-f] [-n 行数] [-p 优先级]", description:"查看systemd日志（统一日志系统）", keywords:["日志","systemd","查看","系统日志","journalctl"], examples:[
      {cmd:"journalctl -u nginx -f", desc:"实时查看nginx服务日志"},
      {cmd:"journalctl --since '1 hour ago'", desc:"查看最近1小时日志"},
      {cmd:"journalctl -p err -n 50", desc:"查看最近50条错误日志"},
      {cmd:"journalctl --since today --until '1 hour ago' -u sshd", desc:"查看指定时间段的sshd日志"}
    ]},
    { name:"awk", category:"log", syntax:"awk '条件 {动作}' 文件", description:"强大的文本处理工具（按列处理、模式匹配）", keywords:["日志","文本","处理","分析","按列","列处理"], examples:[
      {cmd:"awk '{print $1,$7}' access.log", desc:"打印第1和第7列"},
      {cmd:"awk -F: '{print $1}' /etc/passwd", desc:"以冒号分隔，打印用户名"},
      {cmd:"awk '$9==404 {print $7}' access.log", desc:"筛选404请求的URL"},
      {cmd:"awk '{sum+=$5} END {print sum}' access.log", desc:"计算第5列总和"}
    ]},
    { name:"sed", category:"log", syntax:"sed [-i] [-n] '命令' 文件", description:"流编辑器（文本替换、删除、插入）", keywords:["日志","文本","替换","编辑","sed","文本替换"], examples:[
      {cmd:"sed 's/old/new/g' file.txt", desc:"全局替换文本"},
      {cmd:"sed -i 's/debug/info/g' app.conf", desc:"原地替换文件内容"},
      {cmd:"sed -n '10,20p' file.txt", desc:"打印第10-20行"},
      {cmd:"sed '/^#/d' nginx.conf", desc:"删除所有注释行"}
    ]},
    { name:"sort", category:"log", syntax:"sort [-n] [-r] [-k 列] [-t 分隔符] [-u] 文件", description:"对文本行排序", keywords:["日志","文本","排序"], examples:[
      {cmd:"sort -rn -k5 access.log", desc:"按第5列数值逆序排序"},
      {cmd:"sort -t: -k3 -n /etc/passwd", desc:"按UID数值排序"},
      {cmd:"sort -u names.txt", desc:"排序并去重"}
    ]},
    { name:"uniq", category:"log", syntax:"uniq [-c] [-d] [-u] 文件", description:"去除连续重复行（通常配合sort使用）", keywords:["日志","文本","去重","uniq"], examples:[
      {cmd:"sort access.log | uniq -c | sort -rn | head -10", desc:"统计访问最多的前10条记录"},
      {cmd:"sort ips.txt | uniq -d", desc:"只显示重复行"}
    ]},
    { name:"cut", category:"log", syntax:"cut [-d 分隔符] [-f 列] [-c 字符范围] 文件", description:"按列截取文本", keywords:["日志","文本","截取","列","cut","分割"], examples:[
      {cmd:"cut -d: -f1 /etc/passwd", desc:"提取所有用户名"},
      {cmd:"cut -c1-10 access.log", desc:"截取每行前10个字符"}
    ]},
    { name:"xargs", category:"log", syntax:"命令 | xargs [-I {}] [-n 数量] [-P 并发数] 命令", description:"将标准输入转为命令参数", keywords:["日志","文本","参数","管道","xargs","批量处理"], examples:[
      {cmd:"find . -name '*.py' | xargs grep 'import'", desc:"搜索所有py文件中的import"},
      {cmd:"cat urls.txt | xargs -P 5 -I {} curl -s {}", desc:"并发5个请求"},
      {cmd:"find /tmp -type f -mtime +7 | xargs rm -f", desc:"删除7天前的临时文件"}
    ]},

    { name:"tr", category:"log", syntax:"tr [选项] 字符集1 [字符集2]", description:"字符替换、删除、压缩重复字符", keywords:["文本","替换","删除","字符","转换"], examples:[
      {cmd:"echo 'hello' | tr 'a-z' 'A-Z'", desc:"小写转大写"},
      {cmd:"tr -d '\\r' < dos.txt > unix.txt", desc:"删除回车符"},
      {cmd:"tr -s '\\n' < file.txt", desc:"压缩连续空行"}
    ]},
    { name:"strings", category:"log", syntax:"strings [-n 最小长度] 文件", description:"从二进制文件中提取可打印的字符串", keywords:["二进制","字符串","提取","分析"], examples:[
      {cmd:"strings /bin/ls | head -20", desc:"查看可执行文件中的字符串"},
      {cmd:"strings -n 8 core.dump", desc:"提取长度≥8的字符串"}
    ]},
    { name:"logrotate", category:"log", syntax:"logrotate [-f] [-d] [-v] 配置文件", description:"日志轮转工具，按大小/时间自动切割归档", keywords:["日志","轮转","切割","归档","管理"], examples:[
      {cmd:"logrotate -f /etc/logrotate.conf", desc:"强制执行日志轮转"},
      {cmd:"logrotate -d /etc/logrotate.d/nginx", desc:"调试模式，预览切割计划"},
      {cmd:"logrotate -v /etc/logrotate.d/nginx", desc:"详细模式执行"}
    ]},
    { name:"nl", category:"log", syntax:"nl [-b 样式] [-n 格式] 文件", description:"给文件内容添加行号", keywords:["行号","编号","文本","显示"], examples:[
      {cmd:"nl file.txt", desc:"给文件加行号"},
      {cmd:"nl -b a file.txt", desc:"所有行都编号（包括空行）"}
    ]},
    { name:"column", category:"log", syntax:"column [-t] [-s 分隔符] 文件", description:"将文本格式化为对齐的列", keywords:["文本","格式化","列","对齐","表格"], examples:[
      {cmd:"mount | column -t", desc:"格式化mount输出为对齐表格"},
      {cmd:"cat /etc/passwd | column -t -s :", desc:"以冒号分隔格式化为表格"}
    ]},

    // ==================== 权限管理 ====================
    { name:"chmod", category:"permission", syntax:"chmod [-R] [数字模式|符号模式] 文件/目录", description:"修改文件或目录权限", keywords:["权限","文件","修改","chmod","修改权限","设置权限"], examples:[
      {cmd:"chmod 755 script.sh", desc:"设置rwxr-xr-x权限"},
      {cmd:"chmod -R 644 /var/www/html", desc:"递归设置目录下文件权限"},
      {cmd:"chmod u+x,g+w file.txt", desc:"符号模式：所有者加执行，组加写"}
    ]},
    { name:"chown", category:"permission", syntax:"chown [-R] [用户][:组] 文件/目录", description:"修改文件或目录的所有者和所属组", keywords:["权限","文件","所有者","属主","chown","修改所有者"], examples:[
      {cmd:"chown nginx:nginx /var/www/html", desc:"修改所有者和组为nginx"},
      {cmd:"chown -R root:root /etc/nginx", desc:"递归修改所有者"},
      {cmd:"chown :staff file.txt", desc:"只修改所属组"}
    ]},
    { name:"chgrp", category:"permission", syntax:"chgrp [-R] 组名 文件/目录", description:"修改文件或目录的所属组", keywords:["权限","文件","组","chgrp","修改组"], examples:[
      {cmd:"chgrp developers project/", desc:"修改目录所属组"},
      {cmd:"chgrp -R staff /shared/", desc:"递归修改所属组"}
    ]},
    { name:"sudo", category:"permission", syntax:"sudo [-u 用户] [-l] 命令", description:"以超级用户（或其他用户）身份执行命令", keywords:["权限","提权","管理员","root","sudo","管理员权限"], examples:[
      {cmd:"sudo systemctl restart nginx", desc:"以root身份重启nginx"},
      {cmd:"sudo -u postgres psql", desc:"以postgres用户执行命令"},
      {cmd:"sudo -l", desc:"查看当前用户的sudo权限"}
    ]},
    { name:"su", category:"permission", syntax:"su [-] [用户名]", description:"切换用户身份", keywords:["权限","切换","用户","su","切换用户"], examples:[
      {cmd:"su -", desc:"切换到root用户（加载环境变量）"},
      {cmd:"su - postgres", desc:"切换到postgres用户"}
    ]},
    { name:"visudo", category:"permission", syntax:"visudo [-c] [-f 文件]", description:"安全编辑sudoers文件（带语法检查）", keywords:["权限","sudo","配置","sudoers"], examples:[
      {cmd:"visudo", desc:"编辑/etc/sudoers文件"},
      {cmd:"visudo -c", desc:"检查sudoers语法"}
    ]},
    { name:"umask", category:"permission", syntax:"umask [掩码值] [-S]", description:"查看和设置新建文件的默认权限掩码", keywords:["权限","默认","掩码","umask"], examples:[
      {cmd:"umask", desc:"查看当前权限掩码"},
      {cmd:"umask 022", desc:"设置默认掩码（新文件644，新目录755）"},
      {cmd:"umask -S", desc:"以符号形式显示默认权限"}
    ]},
    { name:"setfacl", category:"permission", syntax:"setfacl [-m] [-x] [-R] [-b] 规则 文件", description:"设置文件ACL（访问控制列表）", keywords:["权限","ACL","访问控制","setfacl"], examples:[
      {cmd:"setfacl -m u:john:rwx /shared/project", desc:"给用户john设置rwx权限"},
      {cmd:"setfacl -R -m g:dev:rw /shared/", desc:"给dev组递归设置rw权限"}
    ]},
    { name:"getfacl", category:"permission", syntax:"getfacl [-R] 文件/目录", description:"查看文件ACL（访问控制列表）", keywords:["权限","ACL","访问控制","getfacl","查看权限"], examples:[
      {cmd:"getfacl /shared/project", desc:"查看文件的ACL"},
      {cmd:"getfacl -R /shared/", desc:"递归查看目录下所有ACL"}
    ]},
    { name:"passwd", category:"permission", syntax:"passwd [用户名] [-l] [-u] [-d] [--status]", description:"修改用户密码或管理密码状态", keywords:["权限","密码","用户","修改密码"], examples:[
      {cmd:"passwd", desc:"修改当前用户密码"},
      {cmd:"passwd john", desc:"修改john的密码"},
      {cmd:"passwd -l john", desc:"锁定用户john"}
    ]},
    { name:"useradd", category:"permission", syntax:"useradd [-m] [-s shell] [-G 组] 用户", description:"创建新用户账户", keywords:["权限","用户","创建","添加用户","新建用户"], examples:[
      {cmd:"useradd -m -s /bin/bash john", desc:"创建用户并生成家目录"},
      {cmd:"useradd -G docker,nginx deploy", desc:"创建用户并加入附加组"}
    ]},
    { name:"userdel", category:"permission", syntax:"userdel [-r] 用户", description:"删除用户账户", keywords:["权限","用户","删除","删除用户"], examples:[
      {cmd:"userdel john", desc:"删除用户john"},
      {cmd:"userdel -r john", desc:"删除用户并删除家目录"}
    ]},
    { name:"usermod", category:"permission", syntax:"usermod [-aG 组] [-s shell] [-d 家目录] 用户", description:"修改用户账户属性", keywords:["权限","用户","修改","修改用户"], examples:[
      {cmd:"usermod -aG docker john", desc:"将john加入docker组"},
      {cmd:"usermod -s /bin/zsh john", desc:"修改john的登录Shell"}
    ]},
    { name:"id", category:"permission", syntax:"id [用户]", description:"查看用户的UID/GID和所属组", keywords:["权限","用户","身份","id","用户信息"], examples:[
      {cmd:"id", desc:"显示当前用户的UID/GID和所属组"},
      {cmd:"id john", desc:"查看john的用户信息"}
    ]},
    { name:"who", category:"permission", syntax:"who [-a] [-b] [-r]", description:"查看当前登录系统的用户", keywords:["权限","用户","登录","who","当前用户"], examples:[
      {cmd:"who", desc:"查看当前登录用户"},
      {cmd:"who -b", desc:"查看系统最近启动时间"}
    ]},
    { name:"w", category:"permission", syntax:"w [用户]", description:"查看登录用户及其当前活动", keywords:["权限","用户","登录","活动","w"], examples:[
      {cmd:"w", desc:"查看所有登录用户及其活动"},
      {cmd:"w john", desc:"只看john的活动"}
    ]},
    { name:"last", category:"permission", syntax:"last [-n 数量] [用户]", description:"查看用户登录历史记录", keywords:["权限","用户","登录","历史","last","登录记录"], examples:[
      {cmd:"last -10", desc:"查看最近10次登录记录"},
      {cmd:"last john", desc:"查看john的登录历史"}
    ]},
    { name:"groups", category:"permission", syntax:"groups [用户名]", description:"查看用户所属的所有组", keywords:["用户","组","查看","权限"], examples:[
      {cmd:"groups", desc:"查看当前用户所属组"},
      {cmd:"groups john", desc:"查看john所属的组"}
    ]},
    { name:"chage", category:"permission", syntax:"chage [-l] [-M 天数] [-E 日期] 用户", description:"管理用户密码过期策略", keywords:["密码","过期","策略","安全","用户"], examples:[
      {cmd:"chage -l john", desc:"查看john的密码过期信息"},
      {cmd:"chage -M 90 john", desc:"设置密码90天后过期"},
      {cmd:"chage -E 2025-12-31 john", desc:"设置账户到期日期"}
    ]},
    { name:"ulimit", category:"permission", syntax:"ulimit [-n 数量] [-u 数量] [-a]", description:"查看和设置系统资源限制（文件句柄数/进程数等）", keywords:["限制","资源","文件句柄","进程数","安全"], examples:[
      {cmd:"ulimit -a", desc:"查看所有资源限制"},
      {cmd:"ulimit -n 65535", desc:"设置最大打开文件数为65535"}
    ]},
    { name:"chattr", category:"permission", syntax:"chattr [+i] [-i] [+a] [-a] 文件", description:"修改文件扩展属性（如不可变/仅追加）", keywords:["文件","属性","不可变","保护","安全"], examples:[
      {cmd:"chattr +i important.conf", desc:"设置文件不可修改/删除（需root）"},
      {cmd:"chattr -i important.conf", desc:"取消不可变属性"},
      {cmd:"chattr +a app.log", desc:"设置文件仅可追加写入"}
    ]},
    { name:"lsattr", category:"permission", syntax:"lsattr [-d] [-R] [文件/目录]", description:"查看文件的扩展属性", keywords:["文件","属性","查看","保护"], examples:[
      {cmd:"lsattr /etc/shadow", desc:"查看文件的扩展属性"},
      {cmd:"lsattr -R /etc/nginx/", desc:"递归查看目录下所有文件属性"}
    ]},
    { name:"ssh-copy-id", category:"permission", syntax:"ssh-copy-id [-p 端口] user@host", description:"复制SSH公钥到远程服务器（免密登录配置）", keywords:["SSH","密钥","免密","登录","远程"], examples:[
      {cmd:"ssh-copy-id user@192.168.1.1", desc:"复制公钥实现免密登录"},
      {cmd:"ssh-copy-id -p 2222 user@host", desc:"指定端口复制公钥"}
    ]}
  ]
};

// ============ 搜索工具函数 ============

/**
 * 搜索命令：按命令名或功能关键词双向检索
 * @param {string} query - 搜索关键词
 * @param {string|null} category - 可选，限定分类ID
 * @returns {Array} 匹配的命令列表
 */
function searchCommands(query, category) {
  if (!query || !query.trim()) {
    var cmds = OPS_COMMANDS.commands;
    if (category) cmds = cmds.filter(function(c) { return c.category === category; });
    return cmds;
  }
  var q = query.trim().toLowerCase();
  var keywords = q.split(/\s+/); // 支持多关键词空格分隔
  return OPS_COMMANDS.commands.filter(function(c) {
    if (category && c.category !== category) return false;
    return keywords.every(function(kw) {
      return c.name.toLowerCase().indexOf(kw) !== -1
          || c.keywords.some(function(k) { return k.toLowerCase().indexOf(kw) !== -1; })
          || c.description.toLowerCase().indexOf(kw) !== -1
          || c.syntax.toLowerCase().indexOf(kw) !== -1;
    });
  });
}

/**
 * 高亮文本中的匹配关键词
 * @param {string} text - 原始文本
 * @param {string} query - 搜索关键词
 * @returns {string} 带 <mark> 标签的HTML
 */
function highlightText(text, query) {
  if (!query || !query.trim()) return text;
  var keywords = query.trim().split(/\s+/);
  var result = text;
  keywords.forEach(function(kw) {
    if (!kw) return;
    var re = new RegExp('(' + kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
    result = result.replace(re, '<mark>$1</mark>');
  });
  return result;
}
