"""`python -m privacy_operator` 실행 진입점."""

import sys


def main() -> int:
    from privacy_operator import netguard

    netguard.install()  # 무엇을 불러오기 전에. 내 컴퓨터 주소(127.0.0.1) 말고는 연결하지 못한다
    from privacy_operator.ui.app import main as run

    return run()


if __name__ == "__main__":
    sys.exit(main())
