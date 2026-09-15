# Playlist Player Download Service

`yt-dlp`, FFmpeg, Deno를 사용해 Playlist Player의 현재 영상을 변환하는 원격 보조 서비스입니다.

- MP4: 144p~2160p 중 영상이 지원하는 화질 선택
- 오디오: MP3 또는 M4A, 192kbps
- 영상 2시간, 결과 파일 1.5GB 제한
- IP당 시간당 3개 작업, 서버 전체 동시 처리 1개
- 완료 파일은 30분 후 자동 삭제

## Render 배포

이 폴더를 별도 GitHub 저장소의 루트로 올린 뒤 Render에서 `render.yaml` Blueprint를 배포합니다.
무료 인스턴스는 15분 동안 요청이 없으면 잠들며 첫 요청에서 약 1분의 준비 시간이 생길 수 있습니다.

서비스 URL을 바꾸면 프런트엔드 `js/app.js`의 `DOWNLOAD_SERVICE_URL`도 함께 수정합니다.

## 주의

본인이 다운로드할 권리가 있는 콘텐츠에만 사용해야 합니다. 비공개·로그인 필요 영상과 재생목록 전체 다운로드는 지원하지 않습니다.
