// KWin 6 foreground identity only. Never access window captions or document names.
function report() {
    const window = workspace.activeWindow;
    const app = window && !window.desktopWindow && !window.dock
        ? String(window.desktopFileName || window.resourceClass || '') : '';
    callDBus('org.unityconnect.WellBeing', '/org/unityconnect/WellBeing',
             'org.unityconnect.WellBeing1', 'ReportKWin', app);
}
workspace.windowActivated.connect(report);
const heartbeat = new QTimer();
heartbeat.interval = 5000;
heartbeat.timeout.connect(report);
heartbeat.start();
report();
