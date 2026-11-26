
import os
import socket
import psycopg
from dotenv import load_dotenv

load_dotenv()

# ==================== PostgreSQL 配置 ====================

POSTGRES_CONFIG = {
    "host": "pgm-bp1az0735d8li179so.pg.rds.aliyuncs.com",
    "port": 5432,
    "user": "ImageEmbedding",
    "password": "V7mQ2pL9xR4n#K8s",
    "database": "ImageEmbedding",
    "connect_timeout": 5,
}


# ==================== DNS 检测 ====================

def check_dns(host):
    print("\n[1] DNS 域名解析")

    try:
        addresses = socket.getaddrinfo(host, None)
        ips = list(set(item[4][0] for item in addresses))

        print(f"  [成功] {host}")
        print(f"  解析 IP: {ips}")
        return True

    except Exception as e:
        print(f"  [失败] DNS 解析失败: {e}")
        return False


# ==================== TCP 端口检测 ====================

def check_tcp(host, port):
    print(f"\n[2] TCP 端口检测: {host}:{port}")

    try:
        with socket.create_connection(
            (host, port),
            timeout=POSTGRES_CONFIG["connect_timeout"]
        ):
            print(f"  [成功] TCP {port} 端口可以连接")
            return True

    except Exception as e:
        print(f"  [失败] TCP {port} 端口连接失败: {e}")
        return False


# ==================== PostgreSQL 连接检测 ====================

def check_database():
    print("\n[3] PostgreSQL 数据库连接检测")

    if not POSTGRES_CONFIG["password"]:
        print("  [失败] 未设置 POSTGRES_PASSWORD")
        return None

    try:
        conn = psycopg.connect(
            host=POSTGRES_CONFIG["host"],
            port=POSTGRES_CONFIG["port"],
            user=POSTGRES_CONFIG["user"],
            password=POSTGRES_CONFIG["password"],
            dbname=POSTGRES_CONFIG["database"],
            connect_timeout=POSTGRES_CONFIG["connect_timeout"],
        )

        print("  [成功] PostgreSQL 数据库连接成功")
        print(f"  数据库: {POSTGRES_CONFIG['database']}")
        print(f"  用户名: {POSTGRES_CONFIG['user']}")

        return conn

    except psycopg.OperationalError as e:
        print(f"  [失败] 数据库连接失败: {e}")
        return None

    except Exception as e:
        print(f"  [失败] 未知异常: {e}")
        return None


# ==================== SQL 执行检测 ====================

def check_sql(conn):
    print("\n[4] SQL 执行检测")

    try:
        with conn.cursor() as cursor:

            # 基础 SQL 检测
            cursor.execute("SELECT 1")
            result = cursor.fetchone()

            if result and result[0] == 1:
                print("  [成功] SELECT 1 执行正常")

            # 查询数据库信息
            cursor.execute("""
                SELECT
                    current_database(),
                    current_user,
                    inet_server_addr(),
                    version()
            """)

            database, user, server_ip, version = cursor.fetchone()

            print(f"  数据库名称: {database}")
            print(f"  当前用户: {user}")
            print(f"  数据库 IP: {server_ip}")
            print(f"  PostgreSQL: {version.split(',')[0]}")

            # 查询连接信息
            cursor.execute("""
                SELECT
                    pg_backend_pid(),
                    current_setting('server_encoding'),
                    current_setting('TimeZone')
            """)

            pid, encoding, timezone = cursor.fetchone()

            print(f"  Backend PID: {pid}")
            print(f"  字符编码: {encoding}")
            print(f"  时区: {timezone}")

            print("  [成功] SQL 查询执行正常")
            return True

    except Exception as e:
        print(f"  [失败] SQL 执行异常: {e}")
        return False


# ==================== 主程序 ====================

def main():
    config = POSTGRES_CONFIG

    print("=" * 55)
    print("          PostgreSQL 连通性检测")
    print("=" * 55)

    print(f"Host     : {config['host']}")
    print(f"Port     : {config['port']}")
    print(f"User     : {config['user']}")
    print(f"Database : {config['database']}")
    print(f"Timeout  : {config['connect_timeout']}s")

    # 1. DNS 检测
    if not check_dns(config["host"]):
        print("\n检测结束：DNS 解析失败")
        return

    # 2. TCP 检测
    if not check_tcp(config["host"], config["port"]):
        print("\n检测结束：PostgreSQL TCP 端口无法连接")
        return

    # 3. 数据库连接检测
    conn = check_database()

    if conn is None:
        print("\n检测结束：数据库连接失败")
        return

    try:
        # 4. SQL 执行检测
        check_sql(conn)

    finally:
        conn.close()
        print("\n数据库连接已关闭")

    print("\n" + "=" * 55)
    print("          PostgreSQL 检测完成")
    print("=" * 55)


if __name__ == "__main__":
    main()
