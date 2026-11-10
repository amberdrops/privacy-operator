"""설치판 진입점. tools/build_installer.py가 이 파일로 PrivacyOperator.exe를 만든다."""

import sys

from privacy_operator.__main__ import main

sys.exit(main())
