import argparse
import secrets
import string
from pathlib import Path


CHARS = string.ascii_letters + string.digits
DOMAIN = "ai-job.online"


def random_token(length: int) -> str:
    return "".join(secrets.choice(CHARS) for _ in range(length))


def generate_accounts(count: int) -> list[tuple[str, str]]:
    accounts: list[tuple[str, str]] = []
    used_emails: set[str] = set()

    while len(accounts) < count:
        local_part = random_token(8)
        email = f"{local_part}@{DOMAIN}"
        if email in used_emails:
            continue
        used_emails.add(email)
        password = random_token(10)
        accounts.append((email, password))

    return accounts


def write_accounts(accounts: list[tuple[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for email, password in accounts:
            f.write(f"{email}: {password}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="批量生成账号密码文件（email: password）"
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=20,
        help="生成数量，默认 20",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="accounts.txt",
        help="输出文件路径，默认 accounts.txt",
    )
    args = parser.parse_args()

    if args.count <= 0:
        raise ValueError("count 必须大于 0")

    output_path = Path(args.output)
    accounts = generate_accounts(args.count)
    write_accounts(accounts, output_path)
    print(f"已生成 {len(accounts)} 条账号到: {output_path}")


if __name__ == "__main__":
    main()
