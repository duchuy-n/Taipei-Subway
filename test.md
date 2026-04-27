# Cuộc trò chuyện về logic routing GIS/Metro

## Người dùng

Nhận xét đi

Đúng, tôi đã quét lại toàn bộ route stack. Logic hiện tại là như này:

### 1. Frontend Chỉ Gửi Điểm, Không Chọn Route

Ở main page, khi bấm Find Route, frontend gửi:

- `start_lon`, `start_lat`
- `end_lon`, `end_lat`
- `via_station_ids`
- `walking_m_per_sec = 1.3`

Tới API `/api/gis/route/points`.

Frontend chỉ vẽ kết quả backend trả về, không tự quyết định route. Xem `gis.js` line 818.

### 2. Backend Áp Admin Scenario Trước

Trong `routes.py` line 960, backend load network rồi áp rule admin:

- Banned station: hard block, ga bị loại khỏi network.
- Blocked segment: hard block, đoạn line bị xóa khỏi graph.
- Rain zone: soft penalty, không cấm ga, chỉ tăng cost đi bộ.

Với blocked segment ngắn, backend có thể tạo `walk_bypass_pairs` để đi bộ bypass giữa hai ga cạnh đoạn bị block.

Phần này nằm ở `admin_scenarios.py` line 80.

### 3. Tìm Candidate Ga Bằng Walking Network

Sau đó backend tìm các ga có thể đi bộ tới từ start/end:

- Start lấy tối đa 10 candidate.
- End lấy tối đa 5 candidate.
- Dùng walk graph OSM, không chỉ dùng khoảng cách chim bay.
- Có Dijkstra trên walking graph để tìm access point gần nhất theo đường đi bộ thật.
- Có smart snapping: xét 5 node gần nhất, nếu snap xa hơn 100m thì phạt 5x để tránh nhảy qua sông/đường lớn.

Logic nằm ở `walk_network.py` line 502.

Nếu walk graph không tìm được access point, nó fallback sang nearest station bằng khoảng cách địa lý.

### 4. Lọc Candidate “Hợp Lý”

Backend không thử mọi ga toàn thành phố. Nó lọc candidate theo khoảng cách đi bộ:

Normal candidates:

```text
distance <= max(best_distance * 2.0, best_distance + 300m)
```

Strategic candidates:

```text
distance <= max(best_distance * 4.0, best_distance + 2200m)
```

Strategic chỉ được chọn nếu tốt hơn normal rõ ràng:

```text
strategic_cost + 180 < normal_cost
and strategic_cost < normal_cost * 0.85
```

Tức là không dễ gì chọn ga xa, trừ khi tổng route tốt hơn nhiều.

### 5. Với Mỗi Cặp Ga Candidate, Chạy A*

Đây là điểm quan trọng: A* chạy giữa các ga, không chạy trực tiếp từ điểm click tới điểm click.

Ví dụ backend thử:

```text
start point -> candidate start station
A* subway route
candidate end station -> end point
```

A* nằm ở `route_engine.py` line 280.

State của A* là:

```text
(station_id, line_id)
```

Graph có các edge:

- ride: đi tàu giữa hai ga cùng line.
- transfer: chuyển line trong cùng ga.
- walk: đi bộ giữa hai ga gần nhau hoặc bypass do admin block.

A* dùng priority queue:

```text
estimated_total = current_cost + heuristic
```

Heuristic là khoảng cách chim bay tới ga đích / tốc độ tàu tối đa 80 km/h.

### 6. Cost Của A*

Cost là tuple:

```text
(time_cost, walking_time, transfer_count, stop_count)
```

Nó so tuple theo thứ tự đó. Nghĩa là ưu tiên:

1. Weighted time cost thấp nhất
2. Ít walking hơn
3. Ít transfer hơn
4. Ít stop hơn

Các edge chính:

```text
ride cost = travel_sec
transfer cost = transfer_sec + line_switch_penalty
walk cost = walk_sec * WALK_COST_PENALTY_FACTOR
```

Hiện tại:

- `WALK_COST_PENALTY_FACTOR = 5.0`
- `line_switch_penalty = 180s`
- `default_transfer_sec = 30s`
- walking speed = 1.3 m/s

Nên app rất không thích đi bộ xa.

### 7. Chọn Cặp Ga Tốt Nhất

Sau khi A* trả route cho từng cặp candidate, backend tính candidate score:

```text
ride_and_transfer_time
+ (route_walk_time + access_walk_time + egress_walk_time) * 5
+ rain_penalty
```

Đây là phần vừa được sửa để đồng bộ với A*. Trước đó candidate selection dùng gần như time thật, nên có lúc chọn ga xa như Dazhi vì route tàu phía sau nhìn nhanh.

Sau fix, nếu Zhongshan Junior High School có walking thực sự gần hơn, nó sẽ thắng trừ khi route tàu sau đó tệ hơn rất nhiều.

### 8. Thời Gian Hiển Thị Không Phải Weighted Cost

UI hiển thị:

```text
total_journey_time_sec =
subway_time
+ access_walk_time
+ egress_walk_time
+ rain_penalty
```

Nó không nhân walking 5x khi hiển thị. Hệ số 5x chỉ dùng để chọn route, không dùng để nói “người dùng mất bao lâu”.

### 9. Render Map

Backend trả:

- `access_walk_path`
- `egress_walk_path`
- `ride_path_features`
- `route.steps`
- `route_diagnostics`

Frontend vẽ:

- ride line theo `ride_path_features`
- đi bộ đầu/cuối theo `access_walk_path`, `egress_walk_path`
- transfer cùng ga bằng marker Transfer
- fallback walk dài không có road path thì không vẽ đường thẳng xuyên map nữa

Nằm ở `gis.js` line 858.

### Tóm Tắt Logic Hiện Tại

Route chọn theo pipeline:

```text
Click points
-> find walk candidates by OSM walking graph
-> remove hard-banned stations
-> keep normal/strategic candidate sets
-> run A* for each candidate station pair
-> score pair with A*-compatible weighted walking cost
-> apply rain soft penalty
-> select best route
-> return actual journey time + diagnostics
-> frontend renders result
```

Nói ngắn gọn: A* đang dùng cho route trong subway graph, còn Dijkstra đang dùng cho walking candidate discovery. Phần chọn ga candidate bây giờ đã được chỉnh để cùng triết lý với A*: đi bộ xa bị phạt mạnh, nên route sẽ tự nhiên hơn.

---

## Trợ lý

Nhận xét: phần mô tả này **rất ổn và khá thuyết phục**. Nó giải thích đúng luồng trách nhiệm giữa frontend/backend, tách rõ walking discovery với subway routing, và đặc biệt làm rõ điểm dễ gây hiểu nhầm: **A\* không chạy từ điểm click đến điểm click, mà chạy giữa các ga candidate**.

Có vài điểm mình sẽ góp ý để làm bản này sắc hơn:

### 1. Logic tổng thể hợp lý

Pipeline hiện tại nghe đúng hướng:

```text
Frontend chỉ gửi input thô -> backend quyết định route -> frontend render kết quả.
```

Điều này tốt vì route logic tập trung ở backend, tránh tình trạng frontend/backend lệch nhau. Việc admin scenario được áp trước candidate search cũng đúng, vì banned station và blocked segment phải ảnh hưởng trực tiếp tới network trước khi tính route.

### 2. Điểm mạnh nhất là đã đồng bộ candidate scoring với A*

Phần quan trọng nhất là đoạn này:

> candidate score dùng walking cost nhân 5, đồng bộ với A*

Đây là fix đúng bản chất. Trước đó nếu candidate selection dùng “thời gian thật” còn A* dùng “weighted cost”, hệ thống có thể chọn cặp ga nghe nhanh về tàu nhưng phi tự nhiên vì phải đi bộ xa.

Sau fix, route selection và A* cùng một triết lý:

```text
đi bộ xa = rất đắt
chuyển line = hơi đắt
đi tàu = bình thường
```

Điều này sẽ làm route “giống người thật chọn” hơn.

### 3. Dijkstra walking graph dùng đúng chỗ

Bạn nói rất đúng:

> A* dùng cho subway graph, Dijkstra dùng cho walking candidate discovery.

Đây là cách tách bài toán hợp lý. Walking graph dùng để tìm ga tiếp cận thực tế theo đường đi bộ, không dùng chim bay. Subway graph dùng A* vì có heuristic theo khoảng cách đến ga đích.

Smart snapping với penalty 5x nếu snap xa hơn 100m cũng hợp lý, vì nó giảm lỗi “nhảy qua sông/đường lớn”. Tuy nhiên nên kiểm tra thực nghiệm ở các khu có cầu vượt, công viên, đường ven sông, hoặc khu station entrance nằm xa node OSM.

### 4. Strategic candidate rule nghe hợp lý nhưng cần log kỹ

Rule:

```text
strategic_cost + 180 < normal_cost
and strategic_cost < normal_cost * 0.85
```

là khá chặt, tốt để tránh chọn ga xa vô lý.

Nhưng mình nghĩ nên log rõ khi strategic thắng, ví dụ:

```json
{
  "selected_candidate_type": "strategic",
  "normal_best_score": "...",
  "strategic_best_score": "...",
  "strategic_margin_sec": "...",
  "reason": "strategic beats normal by >180s and >15%"
}
```

Vì khi user thấy app chọn ga xa hơn, diagnostics cần giải thích được: “tuy đi bộ xa hơn, nhưng tổng weighted score tốt hơn đáng kể”.

### 5. Cần cẩn thận với rain penalty

Bạn ghi:

```text
Rain zone: soft penalty, không cấm ga, chỉ tăng cost đi bộ.
```

và candidate score:

```text
ride_and_transfer_time
+ walking_time * 5
+ rain_penalty
```

Điểm cần kiểm tra là rain penalty có bị cộng hai lần không:

Một lần trong walking/access/egress cost, và một lần ở `rain_penalty`.

Nếu rain penalty là phần bổ sung riêng, ổn. Nhưng nếu walking time đã được inflate do rain zone rồi lại cộng `rain_penalty`, route có thể né mưa quá mạnh. Nên làm rõ trong code/comment:

```text
walking_time = physical walking time
rain_penalty = additional soft cost only for route selection/display
```

hoặc:

```text
walking_cost = walking_time * 5 + rain_penalty
```

Miễn là không double count.

### 6. Hiển thị actual time không dùng weighted cost là đúng

Phần này rất quan trọng và nên giữ:

> UI không nhân walking 5x khi hiển thị.

Đúng. Weighted cost chỉ là utility function để chọn route, không phải ETA thật. Nếu UI hiển thị walking x5 thì user sẽ thấy thời gian vô lý.

Tuy nhiên UI/diagnostics nên có cả hai số:

```text
estimated_journey_time_sec
selection_weighted_cost
```

Một cái cho người dùng, một cái cho debug. Như vậy khi route có vẻ lạ, dev/admin vẫn hiểu vì sao nó được chọn.

### 7. Một rủi ro: fallback nearest station bằng geographic distance

Fallback này cần cẩn thận. Nếu walk graph không tìm được access point, nearest station bằng chim bay có thể tạo route kỳ quặc, đặc biệt ở chỗ có sông, đường cao tốc, depot, hoặc địa hình bị chia cắt.

Nên diagnostics đánh dấu rõ:

```json
{
  "access_method": "walk_graph | geo_fallback",
  "fallback_reason": "...",
  "fallback_distance_m": "..."
}
```

Và nếu fallback distance quá xa, có thể trả warning hoặc giảm confidence.

### 8. Nên thêm invariant/test case

Để chắc fix này không regress, nên có vài test case kiểu:

1. **Ga gần hơn thắng** nếu subway route không tệ hơn đáng kể.
2. **Ga xa chỉ thắng** khi tiết kiệm được đủ lớn theo strategic rule.
3. **Blocked segment** tạo bypass nếu hợp lệ, nhưng không xuyên qua hard block.
4. **Rain zone** làm route né đi bộ/mưa nếu có alternative hợp lý.
5. **UI ETA** không bị nhân walking penalty.
6. **Diagnostics** ghi rõ candidate bị loại vì khoảng cách, hard ban, strategic threshold, hoặc A* no path.

### Kết luận

Mô tả này đúng hướng và logic hiện tại nghe khá mature. Điểm fix quan trọng nhất là **candidate selection đã dùng walking penalty cùng triết lý với A\***, nên sẽ giảm đáng kể các route “trông tối ưu trên giấy nhưng người dùng thấy vô lý”.

Nếu mình phải chốt thành một nhận xét ngắn:

> Thiết kế hiện tại hợp lý: frontend chỉ render, backend quyết định route; Dijkstra dùng để tìm ga đi bộ thực tế, A* dùng để tối ưu subway graph; admin scenario được áp trước routing; và candidate scoring đã được sửa để phạt đi bộ xa tương thích với A*. Phần cần chú ý thêm là logging diagnostics, tránh double-count rain penalty, và kiểm soát fallback geographic distance để không sinh route phi tự nhiên.

---

## Người dùng

còn điều gì cần cải thiển không?. Đúng là có những test case nó chọn những ga có vẻ xa hơn để đi ko biết tại sao

---

## Trợ lý

Có. Hiện tượng “chọn ga có vẻ xa hơn” vẫn có thể xảy ra **hợp lý**, nhưng hiện tại có vẻ hệ thống chưa giải thích đủ rõ *vì sao*. Mình sẽ cải thiện theo 3 hướng: **debug visibility**, **route selection constraints**, và **UX/diagnostics**.

### 1. Cần log rõ toàn bộ candidate scoring

Hiện tại nếu chỉ thấy route cuối cùng thì rất khó biết vì sao backend chọn ga xa. Nên trả thêm `route_diagnostics` dạng bảng cho top candidate pairs.

Ví dụ backend nên log/return:

```json
{
  "candidate_pairs": [
    {
      "start_station": "Zhongshan Junior High School",
      "end_station": "Songshan Airport",
      "access_walk_m": 280,
      "egress_walk_m": 120,
      "subway_time_sec": 720,
      "transfer_count": 1,
      "route_walk_time_sec": 0,
      "access_walk_time_sec": 215,
      "egress_walk_time_sec": 92,
      "weighted_walk_cost_sec": 1535,
      "line_switch_penalty_sec": 180,
      "rain_penalty_sec": 0,
      "final_score": 2435,
      "selected": false,
      "reject_reason": "higher_score"
    },
    {
      "start_station": "Dazhi",
      "end_station": "Songshan Airport",
      "access_walk_m": 950,
      "egress_walk_m": 120,
      "subway_time_sec": 240,
      "transfer_count": 0,
      "route_walk_time_sec": 0,
      "access_walk_time_sec": 731,
      "egress_walk_time_sec": 92,
      "weighted_walk_cost_sec": 4115,
      "line_switch_penalty_sec": 0,
      "rain_penalty_sec": 0,
      "final_score": 4355,
      "selected": false,
      "reject_reason": "walking_too_expensive"
    }
  ]
}
```

Quan trọng nhất là phải nhìn được:

```text
access_walk_m
access_walk_time_sec
subway_time_sec
transfer_count
weighted_walk_cost
final_score
candidate_type = normal | strategic | fallback
snap_method = walk_graph | geo_fallback
```

Nếu không có bảng này, mình chỉ đang đoán route engine nghĩ gì.

### 2. Cần phân biệt “xa theo chim bay” và “xa theo đường đi bộ thật”

Có test case nhìn trên bản đồ thấy ga A gần hơn, nhưng OSM walking graph có thể tính ga B dễ đi hơn vì:

```text
ga A gần theo đường thẳng nhưng bị sông, đường lớn, công viên, block, entrance xa
ga B xa hơn theo chim bay nhưng có đường đi bộ trực tiếp hơn
```

Nên diagnostics cần hiện cả hai:

```json
{
  "station": "Zhongshan Junior High School",
  "straight_line_distance_m": 310,
  "walking_network_distance_m": 780,
  "snap_penalty_applied": true
}
```

Nếu một ga “nhìn gần” nhưng walking network distance cao, đó có thể là lý do đúng.

### 3. Cần kiểm tra xem strategic candidate có đang quá rộng không

Rule hiện tại:

```text
strategic distance <= max(best_distance * 4.0, best_distance + 2200m)
```

Cái này có thể cho phép ga khá xa lọt vào candidate set.

Ví dụ best station cách 300m:

```text
normal threshold = max(600m, 600m) = 600m
strategic threshold = max(1200m, 2500m) = 2500m
```

Tức là strategic có thể xét ga cách tới 2.5km. Dù sau đó có rule “phải tốt hơn rõ ràng”, vẫn nên kiểm tra xem nó có vô tình chọn ga xa vì subway leg quá ngắn.

Mình sẽ cân nhắc đổi strategic gate thành chặt hơn:

```text
strategic_distance <= min(
  max(best_distance * 3.0, best_distance + 1200m),
  1800m
)
```

hoặc thêm hard cap:

```text
strategic_access_walk_m <= 1600
strategic_egress_walk_m <= 1600
```

Vì với app metro, đi bộ 2.2km tới ga thường đã là route khó chấp nhận, trừ khi user bật chế độ “allow long walk”.

### 4. Nên thêm “maximum acceptable walk” riêng cho access/egress

Hiện tại walking bị phạt bằng cost, nhưng vẫn có thể thắng nếu subway route tiết kiệm rất nhiều. Nhưng từ góc nhìn người dùng, có một ngưỡng hard UX:

```text
đi bộ 1.5km tới ga gần như luôn bị xem là kỳ lạ
```

Nên có thể thêm rule:

```text
default_max_access_walk_m = 1200
default_max_egress_walk_m = 1200
```

Rồi strategic chỉ được vượt ngưỡng nếu:

```text
saves_at_least_sec >= 600
and transfer_count_reduction >= 1
```

Hoặc đơn giản hơn:

```text
Nếu có normal candidate route hợp lệ, không chọn route có access_walk_m > normal_access_walk_m + 700m
trừ khi tổng actual journey time tiết kiệm hơn ít nhất 8 phút.
```

### 5. Candidate score nên có “perceived weirdness penalty”

Người dùng không chỉ ghét đi bộ lâu; họ ghét route “ngược hướng” hoặc “đi xa bất thường”. Có thể thêm penalty mềm:

```text
extra_access_walk_penalty =
  max(0, access_walk_m - best_access_walk_m - 400) * penalty_per_meter
```

Ví dụ:

```text
nếu ga được chọn xa hơn ga gần nhất hơn 400m
thì cộng thêm penalty
```

Pseudo:

```python
extra_access_m = max(0, access_walk_m - best_start_walk_m - 400)
extra_egress_m = max(0, egress_walk_m - best_end_walk_m - 400)

weirdness_penalty = (
    extra_access_m / walking_m_per_sec * 2.0
    + extra_egress_m / walking_m_per_sec * 2.0
)
```

Lúc đó route xa vẫn có thể thắng, nhưng phải thắng rõ ràng hơn.

### 6. Cần kiểm tra có đang dùng nhầm distance/time trong score không

Một bug rất hay gặp ở pipeline kiểu này:

```text
candidate filtering dùng walking_network_distance
candidate scoring dùng fallback geo distance
UI dùng access_walk_path length
A* dùng route walk edge cost
```

Nếu ba số này không cùng nguồn, route có thể chọn sai.

Nên chuẩn hóa thành một object duy nhất:

```python
AccessCandidate(
    station_id,
    walk_distance_m,
    walk_time_sec,
    walk_path_geometry,
    snap_method,
    straight_line_distance_m,
    confidence
)
```

Sau đó mọi bước dùng cùng `walk_time_sec`.

### 7. Cần hiện lý do chọn route trong UI debug panel

Với test case “sao chọn ga xa vậy?”, UI nên có câu trả lời dạng:

```text
Selected Dazhi because:
- Access walk: +620m compared with nearest station
- Subway time: -11 min
- Transfers: -1
- Final weighted score: 4350 vs 4890
```

Hoặc nếu bất thường:

```text
Warning:
Selected station uses geo fallback, not walking graph. Route confidence is low.
```

Cái này rất hữu ích cho admin/tester.

### 8. Nên có chế độ route preference

Không phải user nào cũng giống nhau. Có thể expose preference:

```text
Fastest
Less walking
Fewer transfers
Rain-safe
Accessible / avoid stairs
```

Với hiện tại, default nên là:

```text
Less weird / balanced
```

Chứ không nên absolute fastest theo weighted score.

Ví dụ config:

```python
ROUTE_PROFILE = {
    "balanced": {
        "walk_penalty": 5.0,
        "max_access_walk_m": 1200,
        "transfer_penalty_sec": 180,
    },
    "less_walking": {
        "walk_penalty": 8.0,
        "max_access_walk_m": 900,
        "transfer_penalty_sec": 120,
    },
    "fastest": {
        "walk_penalty": 2.5,
        "max_access_walk_m": 2000,
        "transfer_penalty_sec": 120,
    }
}
```

### 9. Test case nên lưu cả “expected reason”, không chỉ expected route

Với route engine, đôi khi route khác expected nhưng vẫn hợp lý. Vì vậy test nên assert thêm:

```json
{
  "expected_selected_start_station": "Zhongshan Junior High School",
  "max_access_walk_m": 700,
  "max_extra_walk_vs_nearest_m": 400,
  "reason": "nearest reasonable access should win unless subway saves >8min"
}
```

Hoặc:

```json
{
  "allow_farther_station": true,
  "required_saving_sec": 480,
  "required_diagnostic_reason": "saves_subway_time"
}
```

Như vậy test không bị quá cứng, nhưng vẫn bắt được route phi tự nhiên.

### Mình nghi vấn lớn nhất

Nếu có những case chọn ga xa mà “không biết tại sao”, mình nghi nhất là một trong các nguyên nhân này:

1. **Strategic candidate quá rộng**, cho phép ga xa lọt vào.
2. **Subway time savings đang áp đảo walking penalty**, dù user cảm thấy không đáng.
3. **Walking distance dùng trong score không giống walking path hiển thị.**
4. **Geo fallback được dùng âm thầm**, làm access cost thấp giả tạo.
5. **Rain penalty hoặc blocked segment làm ga gần bị đắt lên**, nhưng diagnostics không hiện rõ.
6. **Normal candidate bị loại sớm do threshold**, nên A* chưa từng thử route từ ga nhìn có vẻ hợp lý.

Ưu tiên cải thiện của mình sẽ là:

```text
1. Return top 10 candidate pair diagnostics.
2. Log rejected candidates with reject_reason.
3. Add hard/soft max access walk cap.
4. Add weirdness penalty for stations much farther than nearest.
5. Show selected route explanation in debug UI.
```

Nhận xét ngắn gọn: **logic hiện tại đúng hướng, nhưng thiếu “explainability”.** Khi route chọn ga xa, hệ thống phải trả lời được: nó xa hơn bao nhiêu, tiết kiệm được gì, score thắng vì yếu tố nào, và có dùng fallback hay không.

---

## Người dùng

tôi nên để thời gian thực hay walk time *5

---

## Trợ lý

Nên dùng **cả hai**, nhưng cho **hai mục đích khác nhau**:

```text
Hiển thị cho user: thời gian thực
Chọn route nội bộ: walk time * penalty
```

Với app routing, mình **không khuyên dùng walk time * 5 làm ETA hiển thị**, vì user sẽ thấy sai. Nhưng mình cũng **không khuyên dùng thời gian thực thuần để chọn route**, vì nó dễ chọn route đi bộ xa chỉ để tiết kiệm vài phút đi tàu.

### Khuyến nghị cụ thể

#### 1. `actual_time` để hiển thị

UI nên hiển thị:

```python
display_total_time =
    access_walk_time
    + subway_ride_time
    + transfer_time
    + egress_walk_time
    + rain_delay_if_you_want
```

Ví dụ:

```text
Estimated time: 24 min
Walk: 7 min
Metro: 15 min
Transfer: 2 min
```

Đây là thời gian người dùng thật sự mất.

#### 2. `weighted_cost` để chọn route

Backend nên chọn route bằng score kiểu:

```python
selection_cost =
    subway_ride_time
    + transfer_time
    + route_walk_time * route_walk_penalty
    + access_walk_time * access_walk_penalty
    + egress_walk_time * egress_walk_penalty
    + rain_penalty
    + weirdness_penalty
```

Tức là **walk time * penalty chỉ là utility cost**, không phải thời gian thật.

#### 3. Không nhất thiết phải dùng đúng `*5` cho mọi loại walk

Mình nghĩ `*5` hơi mạnh nếu áp cho tất cả walking. Nên tách ra:

```python
ACCESS_WALK_PENALTY = 4.0
EGRESS_WALK_PENALTY = 4.0
INTER_STATION_WALK_PENALTY = 6.0
RAIN_WALK_PENALTY = 6.0
```

Lý do:

```text
Đi bộ đầu/cuối chuyến: user chấp nhận hơn
Đi bộ giữa hai ga trong lúc đang route: khó chịu hơn
Đi bộ trong mưa: khó chịu hơn nữa
```

Nếu muốn đơn giản, dùng:

```python
WALK_COST_PENALTY_FACTOR = 4.0
```

thay vì 5.0, rồi thêm hard cap cho access/egress.

#### 4. Vấn đề chính không phải 5 hay thời gian thật, mà là thiếu cap

Nếu chỉ dùng `walk * 5`, route vẫn có thể chọn ga xa nếu tiết kiệm subway đủ nhiều. Vì vậy nên có thêm rule UX:

```python
MAX_NORMAL_ACCESS_WALK_M = 900
MAX_NORMAL_EGRESS_WALK_M = 900
MAX_STRATEGIC_ACCESS_WALK_M = 1500
MAX_STRATEGIC_EGRESS_WALK_M = 1500
```

Và nếu route dùng ga xa hơn ga gần nhất nhiều quá, phải thắng rất rõ:

```python
if access_walk_m > best_access_walk_m + 600:
    require_saving_sec = 480  # phải tiết kiệm ít nhất 8 phút actual time
```

Cái này xử lý tốt hơn việc cứ tăng penalty lên 6, 7, 8.

#### 5. Công thức mình khuyên dùng

Cho default “balanced route”:

```python
display_time_sec = (
    access_walk_time_sec
    + subway_time_sec
    + transfer_time_sec
    + egress_walk_time_sec
    + rain_delay_sec
)
```

```python
selection_cost_sec = (
    subway_time_sec
    + transfer_time_sec
    + access_walk_time_sec * 4.0
    + egress_walk_time_sec * 4.0
    + route_walk_time_sec * 6.0
    + rain_penalty_sec
    + weirdness_penalty_sec
)
```

Thêm weirdness penalty:

```python
extra_access_m = max(0, access_walk_m - nearest_access_walk_m - 400)
extra_egress_m = max(0, egress_walk_m - nearest_egress_walk_m - 400)

weirdness_penalty_sec = (
    extra_access_m / walking_m_per_sec * 2.0
    + extra_egress_m / walking_m_per_sec * 2.0
)
```

### Chốt lại

**Không chọn một trong hai.**

Dùng như này là đúng nhất:

```text
Actual time: dùng để hiển thị ETA.
Weighted walking cost: dùng để chọn route.
```

Và mình sẽ chỉnh từ:

```python
walk_time * 5
```

sang:

```python
access/egress walk_time * 4
inter-station walk_time * 6
+ max walk cap
+ weirdness penalty
```

Như vậy route sẽ ít chọn ga xa vô lý hơn, nhưng vẫn đủ thông minh để chọn ga xa hơn nếu thật sự tiết kiệm nhiều thời gian.

---

## Người dùng

mình vẫn muốn làm sao để có thể đi tối ưu thời gian nhưng quan trọng là ưu tiêu đi tàu nhiều hơn, vì có ai lại thích đi bộ đúng ko?. Và với cả nếu như luôn chọn đi tàu thì với 2 điểm sát nhau chúng ta phải chọn đi bộ chứ đúng ko? ai rảnh chạy ra ga rồi quay lại

---

## Trợ lý

Đúng. Cách đúng không phải là “luôn đi tàu”, mà là:

```text
Ưu tiên đi tàu khi khoảng cách đủ xa,
nhưng nếu hai điểm gần nhau thì đi bộ thẳng.
```

Bạn nên coi bài toán là **so sánh giữa 2 mode**:

```text
Option A: walk-only
Option B: walk -> metro -> walk
```

Sau đó chọn option có **utility tốt nhất**, không chỉ thời gian thật.

### 1. Luôn tính route đi bộ thẳng trước

Trước khi tìm metro route, backend nên tính:

```python
walk_only_time = direct_walk_distance_m / walking_m_per_sec
```

Nếu có walking graph OSM thì dùng OSM path. Nếu không có thì fallback geo distance nhân hệ số quanh co, ví dụ `1.25`.

```python
walk_only_time = osm_walk_time or geo_distance_m * 1.25 / walking_m_per_sec
```

Route walk-only nên thắng ngay nếu:

```python
walk_only_distance_m <= 600
```

hoặc:

```python
walk_only_time <= 8 * 60
```

Vì dưới 5–8 phút thì người dùng thường sẽ đi bộ, không ai xuống metro cho một đoạn quá ngắn.

### 2. Metro chỉ được chọn nếu tốt hơn walk-only đủ rõ

Với hai điểm gần nhau, metro có thể bị score tốt giả nếu riding time ngắn. Nên thêm điều kiện:

```python
metro_actual_time + metro_overhead_sec < walk_only_time
```

Trong đó `metro_overhead_sec` mô phỏng sự phiền phức của việc vào ga, chờ tàu, định hướng, lên/xuống platform.

Ví dụ:

```python
METRO_USE_OVERHEAD_SEC = 5 * 60
```

Nghĩa là metro phải nhanh hơn đi bộ ít nhất khoảng 5 phút mới đáng dùng.

Rule:

```python
if walk_only_time <= 8 * 60:
    choose_walk_only

elif metro_actual_time + 5 * 60 < walk_only_time:
    choose_metro

else:
    choose_walk_only
```

Cái này sẽ giải quyết case “2 điểm sát nhau mà lại chạy ra ga”.

### 3. Đừng dùng “walk * 5” để tính thời gian tối ưu, dùng nó làm discomfort cost

Bạn muốn:

```text
tối ưu thời gian
nhưng ưu tiên đi tàu nhiều hơn đi bộ
```

Thì score nên gồm 2 lớp:

```python
actual_time = ride_time + transfer_time + access_walk_time + egress_walk_time

discomfort_cost =
    access_walk_time * walk_penalty_extra
    + egress_walk_time * walk_penalty_extra
    + transfer_count * transfer_penalty
    + station_entry_overhead
```

Rồi:

```python
selection_score = actual_time + discomfort_cost
```

Không nên hiểu `walk * 5` là “thời gian”. Nó là “độ khó chịu”.

Ví dụ nếu bạn muốn đi tàu nhiều hơn, nhưng không đi bộ quá xa:

```python
selection_score = (
    actual_time
    + access_walk_time * 3.0
    + egress_walk_time * 3.0
    + inter_station_walk_time * 5.0
    + transfer_count * 120
    + metro_overhead_sec
)
```

Lưu ý: vì `actual_time` đã có walking time rồi, nếu bạn cộng thêm `access_walk_time * 3.0`, tổng hiệu ứng là walking bị tính `4x`.

### 4. Nên thêm hard cap để tránh ga xa vô lý

Penalty thôi chưa đủ. Nên có ngưỡng UX:

```python
MAX_ACCESS_WALK_M = 1200
MAX_EGRESS_WALK_M = 1200
```

Nếu route metro có access/egress vượt ngưỡng, loại luôn, trừ khi user chọn profile “Fastest”.

Default nên là:

```python
MAX_ACCESS_WALK_M = 900
MAX_EGRESS_WALK_M = 900
```

Strategic candidate có thể rộng hơn:

```python
MAX_STRATEGIC_ACCESS_WALK_M = 1500
MAX_STRATEGIC_EGRESS_WALK_M = 1500
```

Nhưng strategic chỉ được thắng nếu tiết kiệm nhiều:

```python
strategic_metro_actual_time + 8 * 60 < best_normal_actual_time
```

### 5. Công thức mình khuyên dùng

#### Step 1: Tính walk-only

```python
walk_only = build_walk_only_route(start, end)
```

#### Step 2: Tính metro route tốt nhất

```python
metro = build_best_metro_route(start, end)
```

#### Step 3: So sánh mode

```python
SHORT_WALK_DISTANCE_M = 600
SHORT_WALK_TIME_SEC = 8 * 60
METRO_MUST_SAVE_SEC = 4 * 60

if walk_only.distance_m <= SHORT_WALK_DISTANCE_M:
    return walk_only

if walk_only.time_sec <= SHORT_WALK_TIME_SEC:
    return walk_only

if metro is None:
    return walk_only

if metro.actual_time_sec + METRO_MUST_SAVE_SEC >= walk_only.time_sec:
    return walk_only

return metro
```

Sau đó bên trong `build_best_metro_route`, chọn ga bằng weighted score:

```python
metro.selection_score = (
    metro.actual_time_sec
    + metro.access_walk_time_sec * 3.0
    + metro.egress_walk_time_sec * 3.0
    + metro.route_walk_time_sec * 5.0
    + metro.transfer_count * 120
    + metro.weirdness_penalty_sec
)
```

### 6. Ví dụ trực giác

#### Case A: hai điểm cách nhau 400m

```text
Walk-only: 5 phút
Metro: đi bộ ra ga 4 phút + đi tàu 2 phút + đi bộ ra điểm đến 4 phút = 10 phút
```

Chọn: **đi bộ**.

Dù bạn “ưu tiên đi tàu”, route metro này vô lý.

#### Case B: hai điểm cách nhau 2.5km

```text
Walk-only: 32 phút
Metro: đi bộ 5 phút + đi tàu 6 phút + đi bộ 4 phút = 15 phút
```

Chọn: **metro**.

#### Case C: metro nhanh hơn nhưng phải đi bộ xa

```text
Walk-only: 22 phút
Metro: đi bộ 13 phút + đi tàu 4 phút + đi bộ 3 phút = 20 phút
```

Thời gian thật metro nhanh hơn 2 phút, nhưng user phải đi bộ xa để tiết kiệm quá ít. Chọn: **walk-only hoặc route metro khác ít đi bộ hơn**.

#### Case D: metro phải đi bộ xa nhưng tiết kiệm rất nhiều

```text
Walk-only: 60 phút
Metro: đi bộ 13 phút + đi tàu 12 phút + đi bộ 5 phút = 30 phút
```

Chọn: **metro**, vì tiết kiệm đủ lớn.

### Chốt cấu hình hợp lý

Mình đề xuất default:

```python
WALKING_M_PER_SEC = 1.3

SHORT_WALK_DISTANCE_M = 600
SHORT_WALK_TIME_SEC = 8 * 60

METRO_MUST_SAVE_SEC = 4 * 60

ACCESS_WALK_EXTRA_PENALTY = 3.0
EGRESS_WALK_EXTRA_PENALTY = 3.0
INTER_STATION_WALK_EXTRA_PENALTY = 5.0

MAX_ACCESS_WALK_M = 900
MAX_EGRESS_WALK_M = 900

TRANSFER_PENALTY_SEC = 120
LINE_SWITCH_PENALTY_SEC = 180
```

Nói ngắn gọn:

```text
Đi gần: đi bộ.
Đi vừa/xa: ưu tiên metro.
Metro chỉ thắng walk-only nếu nhanh hơn đủ đáng kể.
Trong các metro route, phạt đi bộ mạnh để ưu tiên ngồi tàu hơn.
```

Đây là behavior giống người dùng thật nhất.

---

## Người dùng

hãy tạo toàn bộ cuộc trò chuyện thành file .md đầy đủ, chi tiết

[ ]
