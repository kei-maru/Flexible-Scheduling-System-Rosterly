# Rosterly / Veludo 詳細技術実装ドキュメント

作成日: 2026-05-25  
対象: System A（Veludo 公開フロント） / System B（Rosterly SaaS Core）

---

## 1. 全体アーキテクチャ

本システムは、公開ブランドサイトである **System A** と、予約・シフト・店舗運営を担う **System B** を分離した構成である。

- **System A**
  - Veludo 向け公開サイト、会員画面、簡易管理画面を担当する。
  - Cast 表示、Footer 表記、Access ページなどブランド固有 UI を持つ。
  - 予約・Cast 主データ・SSO は System B と連携する。

- **System B**
  - Rosterly として複数店舗を扱う SaaS コア。
  - 店舗、スタッフ、予約対象、シフト、サービス、予約、メール、Webhook、Stripe 連携を管理する。
  - 公開予約ページ、スタッフ招待、Discord OAuth、System A 向け SSO/API を提供する。

大きな設計方針は「ブランド表示は A、業務基盤は B」である。  
これにより、Veludo 専用 UI を保ちながら、予約・権限・店舗管理は SaaS として再利用できる。

主な参照ファイル:

- `system_a_veludo/config/settings.py`
- `system_a_veludo/accounts/views.py`
- `system_a_veludo/utils/saas_client.py`
- `system_b_saas/tenants/models.py`
- `system_b_saas/tenants/views.py`
- `system_b_saas/tenants/adapters.py`
- `system_b_saas/dashboard/views.py`
- `system_b_saas/resources/models.py`
- `system_b_saas/bookings/models.py`
- `system_b_saas/bookings/tasks.py`
- `compose.veludo.yml`
- `compose.rosterly.yml`
- `docker-compose.yml`

---

## 2. マルチ店舗設計

System B は `Tenant` を店舗境界として扱うマルチテナント設計である。

`Tenant` は店舗名、slug、ロゴ、契約状態、公開予約設定、必要な顧客入力項目、Webhook URL、API Key、Stripe 情報などを保持する。予約・スタッフ・サービス・メールテンプレートなどの主要データは、Tenant に紐づく。

主なモデル:

- `Tenant`
  - 店舗単位の設定・契約・API 認証情報を保持する。
- `SaaSUser`
  - System B 側のログインユーザー。
  - `tenant` と `role` を持ち、店舗所属と権限を表現する。
- `Resource`
  - 実際に予約される対象。Veludo では Cast に相当する。
  - `tenant`、`external_id`、`linked_user`、`is_active` を持つ。
- `ResourceProfile`
  - 公開プロフィール、タグ、画像 URL、表示順、対応サービスなどを保持する。
- `ServicePreset`
  - 店舗ごとのコース・料金・所要時間を定義する。
- `Availability`
  - スタッフごとの空き枠・シフトを表現する。
- `Booking`
  - 店舗、予約対象、顧客、サービス、開始終了時刻、公開詳細 URL を保持する。

店舗ごとの公開予約リンクは以下の形式で生成される。

```text
/dashboard/book/<tenant_slug>/
```

この URL は `Tenant.slug` により店舗を識別するため、同じ System B 上で複数店舗の予約ページを分離できる。

例:

```text
https://api.rosterlyreverse.com/dashboard/book/veludo/
https://api.rosterlyreverse.com/dashboard/book/another-store/
```

店舗が削除申請中、契約無効、API 停止中、またはコアタイム制など公開予約非対応の場合は、公開予約導線で受付を停止する。

---

## 3. SSO 認証設計

SSO は System A のユーザーが System B の Discord OAuth 認証を経由して、System A 側に安全にログインできるようにするための仕組みである。

### 3.1 SSO の流れ

1. System A のログイン画面で SSO ログインを選択する。
2. System A は利用規約同意画面を表示し、短時間だけ同意状態を session に保存する。
3. System A は `state` と `nonce` を生成し、System B の `/sso/authorize` に遷移する。
4. System B は `client_id` と `redirect_uri` を検証する。
5. System B 未ログインの場合、Discord OAuth に遷移する。
6. Discord 認証後、System B は `SSOAuthCode` を発行する。
7. System B は System A の callback URL に `code` と `state` を付けてリダイレクトする。
8. System A は `state` を検証し、System B の `/api/v1/auth/sso/exchange` に `code` を送る。
9. System B は `client_secret`、`redirect_uri`、code の有効期限と未使用状態を検証する。
10. System B は user 情報、tenant、role、nonce を返す。
11. System A は `nonce` を検証し、A 側の shadow user を作成または更新してログインさせる。

### 3.2 セキュリティ設計

SSO では以下を実装している。

- `state` による CSRF 対策
- `nonce` によるレスポンス取り違え防止
- 認可 code のハッシュ保存
- code の TTL
- code の one-time use
- exchange API の IP レート制限
- `client_id` / `client_secret` / `redirect_uri` の組み合わせ検証
- code 交換後の System B セッション logout

`SSOAuthCode` は raw code を保存せず、SHA-256 hash を保存する。  
交換時は `used_at IS NULL` かつ `expires_at > now` の条件で更新し、同時交換を防止する。

### 3.3 ロール同期

System A からは `a_role` として ADMIN / STAFF / CONSUMER のヒントを渡す。  
System B は既存のスタッフ・管理者権限を不用意に CONSUMER に落とさないよう、tenant 所属や staff フラグを確認して preserving する。

System A 側では System B の user id / discord uid / role を保持し、A 側の権限表示や管理画面導線に反映する。  
SSO 連携後は System B がユーザー権限の source of truth になる。

---

## 4. 権限設計

System B の主要ロールは以下である。

- `ADMIN`
  - 店舗管理者。
  - 店舗設定、スタッフ、Cast CMS、サービス、メールテンプレート、予約一覧、招待リンク、契約設定を操作できる。

- `STAFF`
  - 店舗スタッフまたは Cast。
  - 自分に紐づく Resource、シフト、予約を中心に操作する。

- `CONSUMER`
  - 公開フロントや System A から利用する一般ユーザー。
  - 店舗運営機能には入れない。

権限境界は「ログイン状態」「role」「tenant」「対象オブジェクトの tenant」「対象 Resource の linked_user」によって判断する。

System B の Integration API は、通常のユーザー session ではなく Tenant API Key と署名で認証する。  
`IsTenantAuthorized` は以下を検証する。

- `X-Tenant-Key`
- `X-Tenant-Timestamp`
- `X-Tenant-Signature`
- timestamp の許容時間差
- body hash を含む HMAC signature
- replay cache
- `Tenant.is_api_enabled`

これにより、System A から System B へのサーバー間通信は、API Key だけでなく署名と replay 防止を含む設計になっている。

---

## 5. ユーザー免ログイン設計

公開予約ページは、顧客が System B にログインしなくても予約できるように設計している。

公開予約 URL:

```text
/dashboard/book/<tenant_slug>/
```

公開予約で入力する項目は Tenant ごとに変えられる。

- VRCID
- DiscordID
- Email

必須項目は `Tenant.required_customer_fields` で管理する。

予約作成後は `public_access_token` を発行し、顧客向け詳細ページ URL を作る。

```text
/dashboard/book/detail/<access_token>/
```

この URL により、顧客はログインなしで以下を行える。

- 予約内容の確認
- キャンセル可能時間内のキャンセル
- 通報・報告

トークンは推測しにくいランダム値を使い、予約詳細ページはログインではなく token で認可する。  
これにより、一般顧客にアカウント作成を要求せず、予約完了メールから直接詳細ページへ戻れる。

---

## 6. 招待リンク設計

スタッフ追加は `StaffInvite` による招待リンクで行う。

招待リンク:

```text
/dashboard/invite/<token>/
```

`StaffInvite` は以下を持つ。

- token
- tenant
- role
- max_uses
- used_count
- expires_at
- is_active
- created_by

招待を受けたユーザーは Discord OAuth でログインする。  
OAuth 完了後、adapter が session 内の招待 token を確認し、対象ユーザーに tenant と role を付与する。

招待適用時は `select_for_update()` で招待レコードをロックする。  
これにより、同じ招待リンクが複数リクエストで同時使用されても `used_count` と `is_active` の更新が競合しにくい。

別店舗にすでに STAFF / ADMIN として所属しているユーザーが、別店舗の招待リンクを使った場合は拒否する。  
これは店舗間の権限混線を防ぐためである。

---

## 7. データベース設計

本システムは PostgreSQL を前提としている。

主なテーブル構造は以下である。

### 7.1 Tenant 系

- `Tenant`
  - 店舗設定、契約、API 認証、公開予約設定、Webhook URL を保持する。

- `SaaSUser`
  - Django user を拡張した System B ユーザー。
  - `tenant` と `role` を持つ。

- `SSOAuthCode`
  - System A 向け SSO 認可 code を管理する。

- `StaffInvite`
  - スタッフ招待リンクを管理する。

### 7.2 Resource / Shift 系

- `Resource`
  - 予約対象。
  - `tenant + external_id` に unique 制約がある。

- `ResourceProfile`
  - 公開プロフィール、表示順、タグ、avatar URL、対応コース設定を持つ。

- `ResourceMedia`
  - Cast の画像・動画 URL を保持する。

- `Availability`
  - 空き枠またはシフト枠。

- `RecurringPattern`
  - 定期シフト生成のための曜日・時間帯設定。

- `ScheduleTemplate`
  - スタッフごとのシフトテンプレート。

### 7.3 Service / Booking 系

- `ServicePreset`
  - 店舗ごとのコース名、説明、価格、所要時間。

- `Booking`
  - 予約本体。
  - tenant、resource、customer、selected_service、start/end、status、public token を保持する。

- `BookingReport`
  - 顧客または Cast からの通報・報告。

### 7.4 Email / 通知系

- `EmailTemplate`
  - 店舗ごとの予約確定・キャンセルメール設定。
  - ロゴ、本文、ボタン文言、送信先設定などを保持する。

設計上、業務データには原則として tenant 境界を持たせ、クエリ時に tenant 条件を入れる。  
現在の実装では Resource 経由で tenant を参照するモデルも存在するため、今後さらに堅牢化する場合は全主要テーブルへの `tenant_id` 冗長保持と DB 制約強化が改善候補である。

---

## 8. 予約リンク設計

店舗ごとの予約リンクは `Tenant.slug` で分岐する。

```text
/dashboard/book/<tenant_slug>/
```

公開予約ページでは以下を行う。

1. tenant slug から Tenant を取得する。
2. 削除済み・契約無効・API 停止・非対応店舗を拒否する。
3. 対象店舗の Resource を取得する。
4. ResourceProfile の表示順で Cast を並べる。
5. Resource ごとの対応 ServicePreset を表示する。
6. Availability API で空き枠を取得する。
7. 顧客情報と同意チェックを確認する。
8. Booking を作成する。

サービス変更時はコースの所要時間が変わるため、開始時刻候補を再計算する。  
例えば 60 分コースでは入る時間でも、120 分コースでは枠からはみ出る場合があるため、フロント側で `refreshStartOptions()` を再実行し、表示される開始時刻を現在の course に合わせる。

Demo 管理者 Resource は、展示用として Cast・course・空き時間・確認画面までは表示できる。  
ただし最終予約確定ボタンは disabled のままにし、バックエンド create API でも拒否する。

---

## 9. 重複予約防止設計

予約作成時は、同一 Resource の重複予約を防ぐために時間帯の重なりを検証する。

公開予約 create API では以下を確認する。

- 必須項目の存在
- Tenant の契約状態
- Resource が bookable であること
- ServicePreset の有効性
- 予約開始が現在時刻から 24 時間以上先であること
- 選択時間が Availability 内に収まること
- 既存 CONFIRMED Booking と重ならないこと
- 前後 30 分の buffer を含めた競合がないこと

競合判定は以下の考え方である。

```text
existing.start_time < requested.end_time + buffer
AND
existing.end_time > requested.start_time - buffer
AND
status = CONFIRMED
```

該当する予約が存在する場合は `409 Conflict` を返す。

予約作成は `bookings.services.create_confirmed_booking_with_lock()` に集約している。
公開予約 API と Integration Booking API のどちらも、この service を経由して Booking を作成する。

現在の実装では、`transaction.atomic()` 内で対象 `Resource` を `select_for_update()` し、続いて予約時間をカバーする `Availability` を `select_for_update()` でロックする。
そのロックを持った状態で、「指定時間が Availability 内に収まるか」「前後 buffer を含めて既存 CONFIRMED Booking と重ならないか」を再確認してから Booking を作成する。

この設計により、同じ Resource / Availability に対して複数ユーザーが同時に予約確定を押した場合でも、後続リクエストは先行トランザクションの完了を待つ。
先行処理が予約を作成した後、後続処理は lock 解放後の最新 Booking 状態を見て `409 Conflict` 相当で二重予約を拒否できる。

メール送信や Webhook は `transaction.on_commit()` で Celery に渡す。
これにより、DB commit 前に非同期タスクが予約を読みに行く問題を避けている。

今後の DB 設計強化として、`Booking.availability` を追加し、予約成立時に Availability を left available slot / booked slot / right available slot に分割する案がある。
この booked slot と Booking を 0..1 で紐づけると、予約済み枠を DB 構造としてより明確に表現できる。

---

## 10. 外部サービス連携設計

外部連携は同期処理と非同期処理を分けている。

### 10.1 Discord OAuth

System B のログイン、スタッフ招待、System A 向け SSO で Discord OAuth を使う。  
パスワードを自前で保持しないため、認証情報管理リスクを抑えられる。

### 10.2 System A / System B Integration API

System A は System B の Integration API を呼び出す。

主な用途:

- Cast / Resource 同期
- ResourceProfile 同期
- Availability 操作
- Booking 操作
- identity / role 同期

API 認証は Tenant API Key と HMAC 署名で行う。  
署名対象には HTTP method、path + query、timestamp、body hash を含める。

### 10.3 メール送信

予約確定後、Celery task `process_new_booking` がメールを送信する。  
店舗ごとの `EmailTemplate` があれば使用し、なければ fallback 文面を使う。

送信先は以下を設定できる。

- 顧客
- Cast / Resource email

### 10.4 Webhook

Tenant に `webhook_url` が設定されている場合、予約確定後に外部 URL へ POST する。  
Webhook は予約作成レスポンスを遅くしないよう、予約 commit 後の Celery task 内で実行する。

### 10.5 Stripe

System B は Stripe Checkout / Subscription / Webhook を扱う。  
Webhook では署名を検証し、Tenant の subscription 状態を同期する。

### 10.6 非同期タスク設計

Redis を broker とし、Celery worker が非同期処理を担当する。

非同期化している主な処理:

- 予約確定メール
- キャンセル通知
- Webhook 送信

設計上のポイント:

- ユーザーへの HTTP 応答をメール送信の成否に依存させない。
- `transaction.on_commit()` を使い、DB commit 後に task を enqueue する。
- task 側では booking id から最新状態を再取得する。
- メール送信は retry 可能にする。

---

## 11. Docker コンテナ設計

Docker により、開発環境と本番環境の差分を小さくしている。

### 11.1 共通 Dockerfile

`Dockerfile` は Python 3.12 slim をベースにし、依存ライブラリと Gunicorn をインストールする。  
`app` ユーザーを作成し、非 root でアプリを動かす。

### 11.2 ローカル統合 compose

`docker-compose.yml` はローカルで A/B/DB/Redis/Worker をまとめて起動する構成である。

主な service:

- `system_a`
- `system-b`
- `worker`
- `db`
- `redis`

System A は `veludo_db`、System B と Worker は `saas_db` を使う。

### 11.3 System A 本番 compose

`compose.veludo.yml` は小容量サーバー A 向けの構成である。

- `system_a` のみを起動する。
- DB と Redis は Server B 側を参照する。
- Gunicorn は workers を抑え、threads を使って 1GB サーバーでも動きやすくする。
- `./static_root:/app/static_root`
- `./media:/app/media`

### 11.4 System B 本番 compose

`compose.rosterly.yml` は SaaS Core 側の構成である。

- `rosterly-db`
- `rosterly-redis`
- `rosterly-core`
- `rosterly-worker`

System B は PostgreSQL / Redis / Django / Celery worker を同一 compose で管理する。

---

## 12. デプロイ設計

本番は System A と System B を別サーバーに分ける想定である。

### 12.1 System A

System A は 1GB 程度の小さいサーバーで動かすため、DB と Redis を持たず、Server B の PostgreSQL / Redis を参照する。

基本手順:

```bash
cd /var/www/Veludo_Saas
git pull
docker compose -f compose.veludo.yml exec -T system_a python /app/system_a_veludo/manage.py migrate
docker compose -f compose.veludo.yml exec -T system_a python /app/system_a_veludo/manage.py collectstatic --noinput
docker compose -f compose.veludo.yml restart system_a
```

静的ファイルを追加した場合は、`collectstatic` が必要である。  
例: Access ページ用動画を `system_a_veludo/static/videos/access-guide.mp4` に置いた場合、collectstatic 後に `/static/videos/access-guide.mp4` として配信される。

### 12.2 System B

System B は DB / Redis / API / Worker を持つ。

基本手順:

```bash
cd /home/ubuntu/Veludo_Saas
git pull
docker compose -f compose.rosterly.yml exec -T rosterly-core python /app/system_b_saas/manage.py migrate
docker compose -f compose.rosterly.yml exec -T rosterly-core python /app/system_b_saas/manage.py collectstatic --noinput
docker compose -f compose.rosterly.yml restart rosterly-core rosterly-worker
```

コードのみの変更で migration がない場合、`migrate` は `No migrations to apply` になる。これは正常である。

### 12.3 静的ファイルとメディア

- static
  - ソース: `system_a_veludo/static/` または `system_b_saas/static/`
  - 配信先: `static_root` / `static_root_saas`
  - 反映方法: `collectstatic`

- media
  - ユーザーアップロードや店舗ロゴ。
  - A と B はそれぞれ `./media:/app/media` を持つ。
  - A の画面が B の URL を表示する場合、実体は B の media である可能性がある。

---

## 13. Nginx 設計

Nginx は外部入口として TLS 終端、静的ファイル配信、Django への reverse proxy を担当する。

設計方針:

- 80 番は 443 へ redirect
- TLS 証明書は Nginx で終端
- `/static/` は `static_root` を直接配信
- `/media/` は `media` を直接配信
- アプリへの動的リクエストは Gunicorn コンテナへ proxy
- `X-Forwarded-Proto` と `X-Forwarded-For` を渡す
- Django 側は `SECURE_PROXY_SSL_HEADER` を設定する

System A の例:

```nginx
server {
    listen 443 ssl;
    server_name vr-veludo.com www.vr-veludo.com;

    location /static/ {
        alias /var/www/Veludo_Saas/static_root/;
    }

    location /media/ {
        alias /var/www/Veludo_Saas/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

System B の例:

```nginx
server {
    listen 443 ssl;
    server_name api.rosterlyreverse.com;

    location /static/ {
        alias /home/ubuntu/Veludo_Saas/static_root_saas/;
    }

    location /media/ {
        alias /home/ubuntu/Veludo_Saas/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

Django 側では、信頼する proxy からの `X-Forwarded-For` のみを client IP として扱う。  
これにより、レート制限や監査ログで spoof された IP を信用しすぎない設計にしている。

---

## 14. 監査・レート制限・不正対策

公開予約では IP と fingerprint によるレート制限を実装している。

- 10 分あたり IP 上限
- 1 時間あたり IP 上限
- 同一予約 fingerprint の短時間上限
- honeypot field

SSO exchange でも IP 単位の rate limit を行う。  
Integration API では HMAC 署名と replay cache により、盗聴済みリクエストの再送を防ぐ。

予約ページでは `UserBehaviorEvent` により以下のような行動ログを保存する。

- ページ表示
- Cast クリック
- 予約確認表示
- 予約成功
- ページ滞在時間

これにより、運用分析と不正調査の両方に使える。

---

## 15. 面接回答版

### 15.1 1 分版

このシステムは、店舗向けの予約管理サービスです。  
お客様が予約する画面、スタッフがシフトを管理する画面、店舗管理者が予約やスタッフを管理する画面を作りました。

特徴は、ブランドサイト部分と予約管理システムを分けたことです。  
見た目や店舗ごとの表現は System A に置き、予約、スタッフ、権限、通知などの共通機能は System B に集めました。

ログインしなくても予約できるようにしつつ、予約後は専用リンクから確認やキャンセルができます。  
また、同じ時間に二重予約が入らないように、空き枠と既存予約を確認してから予約を確定します。

裏側では Django、PostgreSQL、Redis、Celery、Docker、Nginx を使い、メール送信や外部通知は非同期で処理しています。

### 15.2 SSO について聞かれた場合

ログインは Discord 認証を使っています。  
System A からログインすると、System B 側で Discord 認証を行い、その結果を安全に System A に返す仕組みにしました。

気をつけた点は、ログイン結果をそのまま信用しないことです。  
一度だけ使える短時間のコードを発行し、System A 側で確認してからログインさせています。これにより、別人のログイン情報を使い回されるリスクを下げています。

### 15.3 マルチテナント設計について聞かれた場合

複数店舗を同じシステムで扱えるように、店舗を `Tenant` という単位で管理しています。  
スタッフ、予約、サービスメニュー、メール設定などはすべて店舗に紐づいています。

予約ページも店舗ごとに URL が分かれます。  
たとえば `/dashboard/book/veludo/` のように、URL の中の店舗名で対象店舗を判断します。これにより、同じアプリでも店舗ごとのデータが混ざらないようにしています。

### 15.4 権限設計について聞かれた場合

権限は大きく 3 つに分けました。

- 管理者は店舗設定やスタッフ管理ができます。
- スタッフは自分のシフトや予約を中心に操作します。
- 一般ユーザーは予約や確認だけできます。

特に注意したのは、一般ユーザーと店舗スタッフを混ぜないことです。  
同じ Discord アカウントでログインしても、その人がスタッフなのか一般ユーザーなのかを確認し、必要以上の権限を渡さないようにしました。

### 15.5 ログイン不要予約について聞かれた場合

お客様にはアカウント作成を求めず、予約フォームだけで予約できるようにしました。  
店舗ごとに必要な入力項目を変えられるので、VRCID、DiscordID、メールアドレスなどを店舗に合わせて設定できます。

予約後は、予約専用の確認リンクをメールで送ります。  
お客様はそのリンクからログインなしで予約確認やキャンセルができます。リンクはランダムな文字列なので、他人が簡単に推測できないようにしています。

### 15.6 重複予約防止について聞かれた場合

予約確定時には、まずそのスタッフの空き時間に本当に入っているかを確認します。  
次に、同じスタッフに同じ時間帯の予約がすでに入っていないかを確認します。

考え方としては、1 つの空き枠に対して予約は 0 件か 1 件だけです。  
同時に 2 人が予約ボタンを押した場合でも、空き枠の行をロックして順番に処理すれば、先に確定した 1 件だけを通し、後の予約は断れます。

また、予約の前後に準備時間として 30 分の余裕を入れ、時間が近すぎる予約も防いでいます。

### 15.7 非同期処理について聞かれた場合

予約が完了したあと、確認メールや外部通知を送ります。  
ただ、メール送信をその場で待つと画面が遅くなるので、Redis と Celery を使って裏側で処理しています。

予約データを保存してから、別の worker がメール送信や Webhook 通知を行います。  
これにより、外部サービスが少し遅くても、予約画面自体は重くなりにくいです。

### 15.8 Docker / デプロイについて聞かれた場合

Docker を使って、アプリ、DB、Redis、worker を同じ形で起動できるようにしました。  
開発環境と本番環境で動き方が大きく変わらないようにするためです。

本番では、System A は小さいサーバーで動かし、重い DB や Redis は System B 側に置いています。  
Nginx は HTTPS、静的ファイル配信、Django への転送を担当します。

### 15.9 いちばん工夫した点

一番工夫したのは、見た目の部分と予約管理の部分を分けたことです。  
System A はブランドサイトとして見た目や体験を担当し、System B は予約、スタッフ、権限、通知などの共通機能を担当します。

この分け方にしたことで、Veludo 専用のデザインを保ちながら、予約システム自体は他の店舗にも展開しやすくなりました。
