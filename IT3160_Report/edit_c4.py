import sys

with open('Chuong/Chuong_4_Ket_qua_thuc_nghiem_va_trien_khai.tex', 'r', encoding='utf-8') as f:
    text = f.read()

old_table = r"""\begin{table}[H]
\centering
\caption{Bảng Benchmark hiệu năng hệ thống (đo trên CPU Intel i7-12700H)}
\label{tab:performance_benchmark}
\begin{tabular}{|p{4.5cm}|c|c|p{4.5cm}|}
\hline
\textbf{Giai đoạn xử lý} & \textbf{Cold Start} & \textbf{Hot Path} & \textbf{Ghi chú} \\ \hline
Nạp I/O và Parsing JSON & 120 ms & < 1 ms & Hot path nạp từ RAM Singleton \\ \hline
Xây dựng đồ thị logic & 30 ms & 0 ms & Chỉ thực hiện 1 lần duy nhất \\ \hline
Precomputation (AP-SP) & 290 ms & 0 ms & Tính toán 11,500+ cặp đường đi \\ \hline
Tìm lộ trình (Routing) & -- & < 0.1 ms & Lookup O(1) trong ma trận \\ \hline
Spatial Snapping (Walking) & 15 ms & 2-5 ms & Sử dụng R-Tree / Spatial Index \\ \hline
\textbf{Tổng cộng} & \textbf{440 ms} & \textbf{2-6 ms} & \textbf{Thời gian phản hồi người dùng} \\ \hline
\end{tabular}
\end{table}"""

new_table = r"""\begin{table}[H]
\centering
\caption{Bảng Benchmark hiệu năng hệ thống theo giai đoạn (đo trên CPU Intel i7-12700H)}
\label{tab:performance_benchmark}
\begin{tabular}{|p{4cm}|p{3cm}|p{3.5cm}|p{3.5cm}|}
\hline
\textbf{Thao tác / Tính toán} & \textbf{Cold Start \newline (Khởi động)} & \textbf{Scenario Rebuild \newline (Admin cập nhật)} & \textbf{Warm Query \newline (Truy vấn Runtime)} \\ \hline
Nạp I/O \& Parsing JSON & $\sim$ 120 ms & $\sim$ 10 ms & < 1 ms (RAM/Cache) \\ \hline
Xây dựng đồ thị logic & $\sim$ 30 ms & $\sim$ 30 ms & 0 ms \\ \hline
Precomputation (AP-SP) & $\sim$ 290 ms & $\sim$ 290 ms & 0 ms \\ \hline
Spatial Snapping & -- & -- & $\sim$ 2 ms \\ \hline
Lookup ma trận (Routing) & -- & -- & < 0.1 ms \\ \hline
\textbf{Tổng thời gian} & \textbf{$\sim$ 440 ms} & \textbf{$\sim$ 330 ms} & \textbf{< 3 ms} \\ \hline
\textbf{Tính chất} & Chạy một lần khi khởi động & Thực hiện khi thay đổi rule & Tương tác realtime ($O(1)$) \\ \hline
\end{tabular}
\end{table}"""

if old_table in text:
    print("Found table, replacing...")
    text = text.replace(old_table, new_table)
else:
    print("WARNING: Table not found! Attempting fallback search...")
    if r"\begin{table}[H]" in text:
        print("Table environment exists, but content exact match failed.")

with open('Chuong/Chuong_4_Ket_qua_thuc_nghiem_va_trien_khai.tex', 'w', encoding='utf-8') as f:
    f.write(text)

print("Done")
