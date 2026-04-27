import sys

with open('Chuong/Chuong_2_Phan_tich_he_thong_va_phan_cong_nhom.tex', 'r', encoding='utf-8') as f:
    text = f.read()

# Make sure we use exact strings without regex compilation
old1 = r"""\section{Thuật toán định tuyến cải tiến}
Để đáp ứng yêu cầu phản hồi nhanh trong môi trường web, hệ thống triển khai phiên bản cải tiến của thuật toán Dijkstra với cơ chế so sánh vector chi phí (Lexicographical Comparison) và điều kiện dừng sớm (Early-stop)."""

new1 = r"""\section{Thuật toán định tuyến và Kiến trúc hai pha}
Để đáp ứng yêu cầu phản hồi cực nhanh (dưới 1 ms) trong môi trường web, hệ thống không chạy thuật toán tìm đường trên đồ thị phẳng ở thời điểm người dùng truy vấn. Thay vào đó, kiến trúc định tuyến được chia tách rõ ràng thành hai pha chuyên biệt:

\subsection{Pha 1: Tiền tính toán ngoại tuyến (Offline / Precomputation)}
Nhằm triệt tiêu độ trễ tính toán, hệ thống giải bài toán All-Pairs Shortest Path (AP-SP) để tính trước toàn bộ cặp đường đi giữa mọi ga. Pha này chỉ kích hoạt vào hai thời điểm:
\begin{itemize}
    \item \textbf{Khi khởi động máy chủ (Cold Start):} Nạp topology, xây dựng đồ thị logic và chạy thuật toán.
    \item \textbf{Khi Admin áp dụng Scenario mới:} Các ràng buộc như khoanh vùng mưa (rain zone) hay cấm đoạn tuyến (block segment) làm thay đổi mạnh cấu trúc mạng. Hệ thống huỷ ma trận cũ và chạy lại toàn bộ AP-SP để sinh ma trận mới.
\end{itemize}

Kết quả của pha này được cache vào RAM (dưới mẫu thiết kế \textit{Singleton}) thành ma trận khoảng cách và \textit{next-hop} phục vụ cho việc tra cứu \textit{O(1)}.

\subsection{Pha 2: Truy vấn thời gian thực (Online / Runtime Query)}
Khi người dùng thực hiện truy vấn định tuyến, luồng xử lý runtime diễn ra rất nhẹ nhàng:
\begin{enumerate}
    \item Nhận toạ độ Origin/Destination.
    \item Ánh xạ (snapping) toạ độ vào ga phù hợp qua mạng đi bộ.
    \item Tra cứu (lookup) ma trận đã được tiền tính toán.
    \item Tái dựng lại chuỗi ga thuộc hành trình (Path reconstruction).
\end{enumerate}

\subsection{Thuật toán Dijkstra đa mục tiêu tại Pha tiền tính toán}
Để giải bài toán AP-SP trong Pha 1, hệ thống triển khai phiên bản cải tiến của thuật toán Dijkstra với cơ chế so sánh vector chi phí (Lexicographical Comparison) và điều kiện dừng sớm (Early-stop) đóng vai trò làm thuật toán lõi."""

if old1 in text:
    print("Found old1, replacing...")
    text = text.replace(old1, new1)
else:
    print("WARNING: old1 not found!")

# Try a more forgiving replacement if exact fails due to whitespace
if "Thuật toán định tuyến cải tiến" in text and old1 not in text:
    print("Found partial match for old1, there might be formatting differences.")

old2 = r"""Thuật toán~\ref{alg:early_stop_dijkstra} sở hữu hai tính chất then chốt giúp tối ưu hóa hệ thống:
\begin{itemize}
    \item \textbf{So sánh từ điển (Lexicographical Comparison):} Hệ thống coi mỗi chi phí là một vector $\mathbf{C} = (T, W, N_{tr}, N_{stop})$.
Phép so sánh $A <^{lex} B$ đảm bảo rằng hệ thống sẽ ưu tiên các hành trình có tổng thời gian ($T$) thấp nhất;
nếu bằng nhau mới xét đến các tiêu chí phụ như thời gian đi bộ ($W$) hay số lần chuyển tuyến ($N_{tr}$).
Điều này phản ánh đúng hành vi thực tế của hành khách khi ưu tiên sự nhanh chóng nhưng vẫn muốn giảm thiểu sự mệt mỏi do đi bộ.
    \item \textbf{Cơ chế dừng sớm (Early-stop):} Do Dijkstra luôn lấy ra từ hàng đợi ưu tiên nút có chi phí nhỏ nhất, nếu tại một thời điểm nào đó chi phí của nút vừa lấy ra đã lớn hơn chi phí tốt nhất tìm được tới đích ($best\_target\_cost$), mọi nhánh phát triển tiếp theo từ nút đó đều không cho kết quả tốt hơn.
Việc \texttt{continue} giúp giảm đáng kể không gian tìm kiếm, đặc biệt hữu ích khi đồ thị MRT có nhiều chặng chuyển tuyến phức tạp.
\end{itemize}"""

new2 = r"""Thuật toán~\ref{alg:early_stop_dijkstra} sở hữu hai tính chất then chốt giúp sinh ma trận AP-SP:
\begin{itemize}
    \item \textbf{So sánh từ điển (Lexicographical Comparison):} Hệ thống coi mỗi chi phí là một vector $\mathbf{C} = (T, W, N_{tr}, N_{stop})$.
Phép so sánh $A <^{lex} B$ đảm bảo rằng hệ thống sẽ ưu tiên các hành trình có tổng thời gian ($T$) thấp nhất;
nếu bằng nhau mới xét đến các tiêu chí phụ như thời gian đi bộ ($W$) hay số lần chuyển tuyến ($N_{tr}$).
Điều này phản ánh đúng hành vi thực tế của hành khách khi ưu tiên sự nhanh chóng nhưng vẫn muốn giảm thiểu sự mệt trước khi đi bộ.
    \item \textbf{Cơ chế dừng sớm (Early-stop):} Do Dijkstra luôn lấy ra từ hàng đợi ưu tiên nút có chi phí nhỏ nhất, nếu chi phí lấy ra đã lớn hơn chi phí tốt nhất đang có ($best\_target\_cost$), các nhánh phát triển tiếp theo đều bị loại bỏ.
Việc \texttt{continue} này đóng vai trò \textbf{sống còn} giúp giảm thiểu thời gian chạy Precomputation AP-SP (tính cho hơn 11,500 cặp đường đi). Nếu không có cơ chế cắt tỉa nhánh này, thời gian chặn (blocking) của hệ thống khi Admin áp dụng các scenario thay đổi cấu trúc mạng sẽ bị kéo dài đáng kể làm ảnh hưởng tới độ sẵn sàng của dịch vụ.
\end{itemize}"""

if old2 in text:
    print("Found old2, replacing...")
    text = text.replace(old2, new2)
else:
    print("WARNING: old2 not found!")

with open('Chuong/Chuong_2_Phan_tich_he_thong_va_phan_cong_nhom.tex', 'w', encoding='utf-8') as f:
    f.write(text)

print("Done")
