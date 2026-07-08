using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace HealthMonitorClient;

public partial class MainWindow : Window
{
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromMilliseconds(50) };
    private readonly RadarDemoSource _demoSource = new();
    private double _phase;

    public MainWindow()
    {
        InitializeComponent();
        SetActiveScreen(RealtimeScreen, RealtimeTab);

        _timer.Tick += (_, _) => Tick();
        _timer.Start();
    }

    private void RealtimeTab_Click(object sender, RoutedEventArgs e) => SetActiveScreen(RealtimeScreen, RealtimeTab);

    private void HistoryTab_Click(object sender, RoutedEventArgs e) => SetActiveScreen(HistoryScreen, HistoryTab);

    private void DeviceTab_Click(object sender, RoutedEventArgs e) => SetActiveScreen(DeviceScreen, DeviceTab);

    private void SetActiveScreen(UIElement activeScreen, Button activeTab)
    {
        RealtimeScreen.Visibility = Visibility.Collapsed;
        HistoryScreen.Visibility = Visibility.Collapsed;
        DeviceScreen.Visibility = Visibility.Collapsed;
        activeScreen.Visibility = Visibility.Visible;

        ResetTab(RealtimeTab);
        ResetTab(HistoryTab);
        ResetTab(DeviceTab);
        activeTab.Background = new SolidColorBrush(Color.FromRgb(238, 242, 246));
        activeTab.BorderBrush = new SolidColorBrush(Color.FromRgb(214, 217, 223));
        activeTab.Foreground = new SolidColorBrush(Color.FromRgb(0, 95, 189));
    }

    private static void ResetTab(Button tab)
    {
        tab.Background = Brushes.Transparent;
        tab.BorderBrush = Brushes.Transparent;
        tab.Foreground = new SolidColorBrush(Color.FromRgb(111, 119, 130));
    }

    private void Tick()
    {
        _phase += 5;
        ClockText.Text = DateTime.Now.ToString("HH:mm:ss", CultureInfo.InvariantCulture);

        // 关键逻辑：Demo 模式也走“雷达帧 -> 协议解析 -> UI 快照”的链路，后续真实串口只替换数据源。
        var snapshot = _demoSource.Next(_phase);

        HeartValue.Text = snapshot.HeartRate.ToString(CultureInfo.InvariantCulture);
        BreathValue.Text = snapshot.BreathRate.ToString(CultureInfo.InvariantCulture);
        MotionText.Text = $"{snapshot.MotionLevel} / 100";
        MotionBar.Width = Math.Max(24, snapshot.MotionLevel * 3.7);
        FrameCountText.Text = snapshot.FrameCount.ToString("N0", CultureInfo.GetCultureInfo("zh-CN"));

        DrawWave(HeartCanvas, Color.FromRgb(0, 139, 26), snapshot.HeartWave);
        DrawWave(BreathCanvas, Color.FromRgb(255, 20, 147), snapshot.BreathWave);
    }

    private static void DrawWave(Canvas canvas, Color color, IReadOnlyList<short> samples)
    {
        var width = canvas.ActualWidth;
        var height = canvas.ActualHeight;
        if (width <= 0 || height <= 0)
        {
            return;
        }

        canvas.Children.Clear();
        DrawGrid(canvas, width, height);

        if (samples.Count < 2)
        {
            return;
        }

        var points = new PointCollection();
        for (var i = 0; i < samples.Count; i++)
        {
            var x = i * width / (samples.Count - 1);
            var y = height / 2 - samples[i] * 1.6;
            points.Add(new Point(x, y));
        }

        canvas.Children.Add(new Polyline
        {
            Points = points,
            Stroke = new SolidColorBrush(color),
            StrokeThickness = 2,
            StrokeLineJoin = PenLineJoin.Round,
        });
    }

    private static void DrawGrid(Canvas canvas, double width, double height)
    {
        var gridBrush = new SolidColorBrush(Color.FromRgb(191, 195, 200)) { Opacity = 0.72 };

        for (var x = 0.0; x <= width; x += 60)
        {
            canvas.Children.Add(new Line
            {
                X1 = x,
                Y1 = 0,
                X2 = x,
                Y2 = height,
                Stroke = gridBrush,
                StrokeDashArray = new DoubleCollection { 3, 3 },
                StrokeThickness = 1,
            });
        }

        for (var y = 28.0; y <= height; y += 48)
        {
            canvas.Children.Add(new Line
            {
                X1 = 0,
                Y1 = y,
                X2 = width,
                Y2 = y,
                Stroke = gridBrush,
                StrokeDashArray = new DoubleCollection { 3, 3 },
                StrokeThickness = 1,
            });
        }

        canvas.Children.Add(new Line
        {
            X1 = 0,
            Y1 = height / 2,
            X2 = width,
            Y2 = height / 2,
            Stroke = new SolidColorBrush(Color.FromRgb(255, 20, 147)),
            StrokeThickness = 2,
        });
    }
}
