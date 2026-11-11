; 개인정보작전실 설치 마법사(Inno Setup 6). 직접 부르지 않고 tools/build_installer.py가 값을 넘겨 부른다.
;   AppVersion: pyproject.toml의 version
;   DistDir:    PyInstaller로 묶고 엔진, 에셋, 안내 파일을 넣은 폴더
;   OutDir:     설치 파일이 나올 폴더
;   IconFile:   설치 파일 아이콘
;
; 관리자 권한 없이 사용자 폴더(%LOCALAPPDATA%\Programs)에 설치한다. 학교, 기관 PC에서 관리자 권한이 없어도 깔린다.
; 제거해도 금고, 작업 폴더, 복원본(문서 폴더 아래)과 설정은 지우지 않는다. 대응표를 잃지 않게.
; 단, 설정 창에서 받은 AI 모델({localappdata}\PrivacyOperator\schift)은 제거할 때 함께 지운다.

[Setup]
AppId={{6F1C2B7E-3D4A-4E8B-9A51-2C7D0E9F4B13}
AppName=개인정보작전실
AppVerName=개인정보작전실 {#AppVersion}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\PrivacyOperator
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutDir}
OutputBaseFilename=PrivacyOperator-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\PrivacyOperator.exe
UninstallDisplayName=개인정보작전실
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[UninstallDelete]
; 설정 창에서 받은 AI 모델(약 1GB)은 앱 폴더 밖에 있어 따로 지운다. 판을 올릴 때는 지우지 않는다(다시 받지 않게)
Type: filesandordirs; Name: "{localappdata}\PrivacyOperator\schift"

[InstallDelete]
; 판을 올릴 때 지난 판의 묶음 파일이 섞이지 않게
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\engine"
Type: filesandordirs; Name: "{app}\assets"

[Icons]
Name: "{autoprograms}\개인정보작전실"; Filename: "{app}\PrivacyOperator.exe"
Name: "{autoprograms}\개인정보작전실 자주 묻는 질문"; Filename: "{app}\자주묻는질문.html"
Name: "{autodesktop}\개인정보작전실"; Filename: "{app}\PrivacyOperator.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PrivacyOperator.exe"; Description: "개인정보작전실 실행"; Flags: nowait postinstall skipifsilent
