import argparse
import secrets
import string
from pathlib import Path


CHARS = string.ascii_letters + string.digits
DOMAIN = "ai-job.online"


def random_token(length: int) -> str:
    return "".join(secrets.choice(CHARS) for _ in range(length))


def _load_existing_emails(output_path: Path) -> set[str]:
    existing_emails: set[str] = set()
    if not output_path.exists():
        return existing_emails

    with output_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            email = line.split(":", 1)[0].strip()
            if email:
                existing_emails.add(email)
    return existing_emails


def generate_accounts(count: int, existing_emails: set[str] | None = None) -> list[tuple[str, str]]:
    accounts: list[tuple[str, str]] = []
    used_emails: set[str] = set(existing_emails or [])

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
    existing_emails = _load_existing_emails(output_path)
    mode = "a" if output_path.exists() else "w"
    with output_path.open(mode, encoding="utf-8") as f:
        for email, password in accounts:
            if email in existing_emails:
                continue
            f.write(f"{email}: {password}\n")
            existing_emails.add(email)


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
    existing_emails = _load_existing_emails(output_path)
    accounts = generate_accounts(args.count, existing_emails=existing_emails)
    write_accounts(accounts, output_path)
    print(f"已生成 {len(accounts)} 条账号到: {output_path}（追加写入，不会删除原有内容）")


if __name__ == "__main__":
    main()
