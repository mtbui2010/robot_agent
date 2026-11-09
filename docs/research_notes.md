# Ghi chép nghiên cứu (2026-10 → 2026-11)

Ba lần nghiên cứu tài liệu (deep research). Mỗi lần: tìm kiếm theo 5 hướng → đọc khoảng 15–25 nguồn →
trích nhận định → mỗi nhận định được 3 agent kiểm tra phản biện (bị bác nếu ≥ 2/3 phản đối) → tổng hợp.
Độ tin cậy: **cao** = nhiều nguồn chính / số liệu rõ; **vừa** = một nguồn hoặc tác giả tự báo cáo;
**thấp** = suy luận của chúng ta từ các nhận định khác.

| # | Câu hỏi | Kết quả chính |
|---|---|---|
| R1 | SAGE đứng ở đâu so với các planner LLM khác; cải tiến gì cho SAGE-v2 | cổng kiểm tra symbolic có lợi rõ; replan có thể hại model nhỏ; lý do lỗi giúp sửa gấp đôi |
| R2 | Kiến trúc chung cho nhiều loại robot (planner, tương tác người, verify, replan, module hoá) | lõi không phụ thuộc robot + "hợp đồng skill" khai báo theo robot + cổng kiểm tra trước thực thi |
| R3 | Các nhóm khác đánh giá cả vòng plan → execute → verify → replan → hỏi người → an toàn ra sao; cách làm cho paper RA-L | chưa ai đánh giá trọn vòng với lỗi hiệu chỉnh từ log thật + LLM nhỏ local → khoảng trống cho paper |

---

## R1. SAGE so với các planner LLM khác

**Nhận định đã kiểm tra**

1. **Planner LLM thuần vẫn thất bại nhiều và dao động lớn** (cao). LoTa-Bench/ALFRED: GPT-3.5 10,6 %,
   GPT-3 21,4 %, GPT-4 40,4 %. Chỉ đổi 12 ví dụ ngẫu nhiên trong prompt: 9,6 % ↔ 17,8 % (LLaMA-2 13B).
   Chọn ví dụ theo **độ giống câu lệnh** tốt hơn chọn ngẫu nhiên. [LoTa-Bench](https://arxiv.org/abs/2402.08178)
2. **Replan không phải lúc nào cũng có lợi** (vừa). LLaMA-2 70B: 21,6 → 24,0 %; LLaMA-2 13B: 21,2 → 17,8 %. [LoTa-Bench]
3. **Lỗi chủ yếu do lập kế hoạch / suy luận, không phải nhận diện** (cao). EmbodiedBench (1.128 task, 24 MLLM):
   EB-ALFRED tốt nhất ~68 %, EB-Manipulation tốt nhất 28,9 %; lỗi GPT-4o ở EB-ALFRED: 55 % planning,
   41 % reasoning, 4 % perception. [EmbodiedBench](https://arxiv.org/abs/2502.09560)
4. **Kiểm tra bằng bộ kiểm tra bên ngoài (symbolic) tốt hơn LLM tự kiểm tra rất nhiều** (cao).
   ProgPrompt: bỏ kiểm tra điều kiện → SR 0,34 → 0,28. ISR-LLM Blocksworld (GPT-3.5, 3 khối): trực tiếp 20 %,
   tự kiểm 37 %, kiểm bên ngoài 70 %; GPT-4: 43 / 60 / 97 %. [ProgPrompt](https://arxiv.org/abs/2209.11302),
   [ISR-LLM](https://huggingface.co/papers/2308.13724)
5. **Kế hoạch dạng chương trình tốt hơn văn tự do** (vừa). ProgPrompt SR 0,40 so với LangPrompt 0,00. [ProgPrompt]
6. **Câu lệnh mơ hồ làm giảm thành công tới 36,9 %**; viết lại câu lệnh cho rõ trước khi lập kế hoạch giúp ích (vừa).
   [REI-Bench](https://arxiv.org/abs/2505.10872)
7. **Cho replanner biết *vì sao* lỗi → sửa thành công gần gấp đôi** (vừa). REFLECT: 79,1 % có giải thích,
   41,9 % không có. [REFLECT](https://arxiv.org/abs/2306.15724)
8. **Phân loại lỗi RoboFail** (vừa): planning 55 % (sai action 21, thiếu action 18, sai thứ tự 11, tham chiếu mơ hồ 5);
   execution 45 % (thực thi mức thấp 15, môi trường 13, động lực bất ngờ như rơi đồ 11, nhận diện 6). [REFLECT]
9. **Bộ giám sát lỗi riêng đưa phản hồi cho planner tăng thành công 21–29 %** (vừa).
   [AHA](https://arxiv.org/abs/2410.00371), [Code-as-Monitor](https://arxiv.org/abs/2412.04455),
   [RoboFAC](https://arxiv.org/abs/2505.12224)

**Đã áp dụng vào SAGE-v2**: cổng kiểm tra điều kiện bằng action model (4), mã lỗi có kiểu + lý do đưa vào sửa
kế hoạch (7), ví dụ chọn theo độ giống câu lệnh với leave-one-out (1), hỏi lại khi mơ hồ (6),
kiểm tra từng bước bằng VLM (9), giới hạn số lần gọi LLM và chừa lượt (2).

**Lưu ý**: số liệu chủ yếu từ model 2022–2024; hiệu ứng tương đối đáng tin hơn con số tuyệt đối.
Bị bác: "bỏ sót đồ vật là lỗi chính khi câu lệnh mơ hồ"; "RoboFAC nhỏ hơn GPT-4o 34,1 %".

---

## R2. Kiến trúc cho nhiều loại robot

**Nhận định đã kiểm tra**

1. **Tính di động giữa robot đến từ lớp "hợp đồng skill"** (cao). Mỗi robot khai báo skill (tên, tham số có kiểu,
   mô tả, điều kiện trước / trong / sau, hiệu ứng) và tự cài đặt; planner + bộ điều phối chung, không phụ thuộc robot.
   SkiROS2: một mô tả skill có nhiều bản cài đặt theo phần cứng. [SkiROS2](https://arxiv.org/abs/2306.17030),
   [khảo sát MPER 2025](https://arxiv.org/pdf/2505.04493)
2. **Framework agent LLM: phần riêng của robot là thư viện action/tool đã đăng ký kèm mô tả** (cao), phụ thuộc
   một chiều từ gói robot sang lõi. [ROSA](https://arxiv.org/html/2410.06472v2), [ROS-LLM](https://arxiv.org/pdf/2406.19741v2),
   [BTGenBot](https://arxiv.org/abs/2403.12761)
3. **Cổng kiểm tra tất định giữa đầu ra LLM và thực thi; không tin kế hoạch zero-shot; prompt cần ví dụ** (vừa).
   Nên từ chối + sửa cục bộ thay vì lặng lẽ bỏ action lạ. [BTGenBot]
4. **Planner symbolic là "người quyết định", LLM dịch ngôn ngữ → mục tiêu / mô hình** (vừa; là quan điểm). [MPER]
5. **Lớp an toàn / quyền riêng, dựa luật, kiểm mọi action trước khi tới phần cứng, ghi log mọi action bị chặn**;
   năng lực đóng gói bằng manifest có phiên bản (thấp). [AEROS](https://arxiv.org/pdf/2604.07039)
6. **Kiểm tra bước nên là một interface**: mỗi skill khai báo cách kiểm (predicate trạng thái, cảm biến, hỏi VLM),
   lõi quyết định xử lý lỗi (vừa). [ROS-LLM], [SkiROS2]
7. **Quyết định khi nào hỏi người bằng cổng bất định đã hiệu chỉnh** (cao): KnowNo (conformal prediction),
   CLARA (rõ / mơ hồ / không khả thi), introspective planning giảm hỏi thừa 51,3 % → 6,4 %.
   [KnowNo](https://arxiv.org/abs/2307.01928), [CLARA](https://arxiv.org/abs/2306.10376),
   [Introspective](https://arxiv.org/abs/2402.06529)
8. **Ngắt giữa chừng: replan ngay khi người nói, hỗ trợ "quay lại lệnh trước"** (vừa). [Hi Robot](https://arxiv.org/abs/2502.19417)

**Đã áp dụng** (ARCH_PROPOSAL, merge 2026-11-02): `contract/` (RobotAdapter + `manifest.yaml`), một action model,
planner plug-in (`module:Class`, entry points), cổng kiểm tra trước thực thi, `base_robot` chứng minh robot thứ hai
không sửa lõi, `observe/` ghi + phát lại, `scenarios` test toàn hệ thống, CLAUDE.md ngắn + docs.

**Chưa có bằng chứng** (theo thông lệ chung, không được kiểm chứng trong lần này): monorepo vs polyrepo,
contract test, tracing / replay, task stack với tạm dừng / tiếp tục có lưu trạng thái.

---

## R3. Đánh giá cả vòng và phương pháp cho paper RA-L

**Các nhóm khác làm gì**

| Chủ đề | Cách làm | Nguồn |
|---|---|---|
| Đánh giá theo module | 4 phần (goal interpretation, subgoal decomposition, action sequencing, transition modeling); đo executability + goal satisfaction; phân loại lỗi hallucination / affordance / thiếu / thừa / sai thứ tự (cao) | [EAI](https://arxiv.org/pdf/2410.07166) |
| Vòng lặp kín | simulator chạy skill, thành công = trạng thái cuối khớp goal condition; giới hạn bước 30 / 20 / 15; phản hồi chữ; replan từ trạng thái mới nhất; chia task theo năng lực (cao) | [EmbodiedBench](https://arxiv.org/pdf/2502.09560), [LoTa-Bench](https://arxiv.org/pdf/2402.08178) |
| Phát hiện + hồi phục | lỗi tiêm có kiểm soát (100 sim + 30 thật); chỉ số Exp / Loc / Co-plan (cao) | [REFLECT](https://arxiv.org/pdf/2306.15724) |
| Tiêm lỗi ngẫu nhiên | xác suất **tác giả tự đặt**; DoReMi 4 seed × 12 episode: SayCan 42 %, IM 46 %, DoReMi 94 %; LLM-GP-BT các mức cố định (cao) | [DoReMi](https://arxiv.org/html/2307.00329v4), [LLM-GP-BT](https://arxiv.org/pdf/2502.07772) |
| Hiệu chỉnh từ log thật | chỉ STRANDS (2017), chỉ cho navigation; báo hồi phục theo lớp lỗi, thành công = không lỗi mới trong 1 phút / 1 m (cao) | [STRANDS](https://arxiv.org/pdf/1604.04384) |
| Hỏi lại cho rõ | KnowNo; AmbiK 1.000 cặp task, 3 loại mơ hồ (Preferences / Common Sense / Safety), chỉ số help rate, correct help rate (cao) | [KnowNo](https://arxiv.org/abs/2307.01928), [AmbiK](https://arxiv.org/html/2506.04089v1) |
| Người dùng do LLM đóng vai | khớp người thật kém: Speak-F1 42–43 % so với luật đơn giản 37,7 % (vừa) | [User sim](https://arxiv.org/abs/2410.23535) |
| An toàn | RoboGuard: plan nguy hiểm > 92 % → < 3 %; SafeAgentBench 750 task; **chỉ an toàn mức ngữ nghĩa, không có khoảng cách vật lý tới người** (cao) | [RoboGuard](https://arxiv.org/abs/2503.07885), [SafeAgentBench](https://github.com/shengyin1224/safeagentbench) |

**Khoảng trống paper có thể lấp**
1. Chưa ai đánh giá trọn vòng plan → execute → verify → replan → hỏi người → an toàn trong một khung.
2. Chưa có xác suất lỗi grasp / nhận diện hiệu chỉnh từ log thật để đánh giá planner.
3. Chưa benchmark nào đo vi phạm khoảng cách an toàn khi planner LLM điều khiển robot.
4. LLM nhỏ 7–8B chạy local trên robot chăm sóc tại nhà ít được nghiên cứu.

**Phương pháp đề xuất** (độ tin cậy thấp: suy luận từ các nhận định trên)

- **Đóng góp**: (C1) SAGE-v2 — planner module cho LLM nhỏ; (C2) khung đánh giá cả vòng: `mock_robot`
  (skill đầy đủ, không hành động, trả thành công / lỗi theo xác suất hiệu chỉnh từ log KAAIR) + người dùng ảo
  có tham số + khoảng cách an toàn, Monte Carlo có seed; (C3) kiểm chứng sim-to-real: thứ hạng planner trên
  `mock_robot` khớp robot thật.
- **mock_robot**: ghi log skill trên robot thật trước (kiểu STRANDS); quét mức lỗi ×0,5 / ×1 / ×2; người dùng ảo
  = mô hình xác suất (không trả lời, lỗi ASR, mơ hồ, ngắt, di chuyển) + LLM chỉ để viết câu; kịch bản mơ hồ
  theo AmbiK; pilot nhỏ với người thật.
- **Chỉ số** (kèm khoảng tin cậy 95 % Wilson / bootstrap; so cặp cùng seed bằng McNemar): task success,
  goal-condition success, executability, bảng lỗi kiểu EAI, **thành công giả**, Loc / Co-plan, **hồi phục theo
  lớp lỗi**, help rate / correct help rate, rejection rate, **vi phạm khoảng cách** + khoảng cách nhỏ nhất,
  số lần gọi LLM, thời gian.
- **Baseline**: kiểu SayCan (không phát hiện lỗi / replan), kiểu Inner Monologue, kiểu LLM-Planner / ProgPrompt
  (= `llm_direct`), KnowNo / Binary / No-Help cho hỏi lại.
- **Ablation**: tắt cổng kiểm tra; tắt sửa / lập lại kế hoạch (bắt buộc vì LoTa-Bench); tắt VLM verify; tắt bộ nhớ
  ví dụ; tắt belief tìm đồ; thay giới hạn LLM call; 7B vs 8B vs model lớn.
- **Robot thật**: 10–20 task × 3–5 lần lặp cho SAGE-v2 + 1–2 baseline; so phân phối kết quả và tương quan thứ hạng
  giữa `mock_robot`, Isaac Sim và robot thật.
- **Lộ trình ~5 tháng**: T1 log robot thật + mock_robot P1 → T2 hiệu chỉnh, người dùng ảo, khoảng cách an toàn,
  bộ chạy Monte Carlo → T3 baseline + ablation → T4 robot thật + pilot người + sim-to-real → T5 viết bài, mở mã.

**Chưa xác minh — phải tự kiểm tra**
- Yêu cầu RA-L (số trang, robot thật có bắt buộc, số lần thử, thống kê): không nhận định nào qua kiểm tra.
  Theo hiểu biết chung: 6 trang + tối đa 2 trang có phí, tính cả tài liệu tham khảo — xác nhận trên
  *Information for Authors*.
- ISO 13482 / ISO 10218 / ISO/TS 15066 (giám sát tốc độ và khoảng cách): đọc trực tiếp tiêu chuẩn.
- Bị bác, không trích: "69 % success / 5 % rejection" của SafeAgentBench; bộ chỉ số + cách tinh chỉnh ε gán cho KnowNo.
- Rủi ro phản biện lớn nhất: `mock_robot` có đúng với thực tế không → C3 và log hiệu chỉnh là bắt buộc.

**Câu hỏi mở**: bao nhiêu log thật là đủ để ước lượng tỷ lệ lỗi grasp / nhận diện; đo vi phạm khoảng cách trong mock
không có động lực học có được chấp nhận không; pilot người thật cần bao nhiêu người; replan có cấu trúc
(lỗi có kiểu + giải thích + sửa phần sau) có tránh được mức giảm LoTa-Bench đo ở model nhỏ không.
