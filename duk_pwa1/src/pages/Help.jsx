import React, { useContext, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { MapPin, Bell, Settings, Search, Clock, ShieldCheck, Bus, HelpCircle } from 'lucide-react';
import { SplashContext } from '../App';
import TopBar from '../components/TopBar';

export default function Help() {
  const navigate = useNavigate();
  const { setSplashReady } = useContext(SplashContext);

  useEffect(() => {
    setSplashReady();
  }, [setSplashReady]);

  return (
    <div className="help-screen" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <TopBar
        showBack
        onBack={() => navigate(-1)}
        title="Help & Guidelines"
      />

      <div className="help-content" style={{ flex: 1, overflowY: 'auto', padding: '16px', paddingBottom: '40px' }}>

        <section className="help-section">
          <h2><ShieldCheck size={18} className="help-icon" /> Getting Started</h2>
          <p>
            Welcome to the <strong>DUK Bus Tracker</strong>! This app is designed exclusively for the Digital University Kerala community to effortlessly track the DUK bus, be notified of delays, get predictions on arrival time, and receive smart alerts.
          </p>
          <p>
            Upon your first login, you need to verify your identity using your <strong>@duk.ac.in</strong> or <strong>@iiitmk.ac.in</strong> email and an OTP. You also need to select your morning boarding/evening destination stops, which helps the app provide personalized ETAs.
          </p>
        </section>

        <section className="help-section">
          <h2><Clock size={18} className="help-icon" /> Route View & Timeline</h2>
          <p>
            The <strong>Route View</strong> is your main dashboard. It provides a chronological timeline of the current bus trip (Morning or Evening).
          </p>
          <ul>
            <li><strong>Live Tracking:</strong> As the bus moves, the timeline progresses, highlighting passed stops and showing the next immediate stop.</li>
            <li><strong>ML-Powered ETA:</strong> The app uses AI to predict delays or early arrivals based on live traffic and historical data.</li>
          </ul>
        </section>

        <section className="help-section">
          <h2><MapPin size={18} className="help-icon" /> Map View & 3D Mode</h2>
          <p>
            Switch to the <strong>Map View</strong> to see the bus on an interactive map.
          </p>
          <ul>
            <li><strong>Auto-Follow:</strong> The map centers automatically on the bus as it moves. If you pan away, tap the <strong>crosshair icon</strong> to re-center.</li>
            <li><strong>Interactive Stops:</strong> Tap any stop on the map to see its scheduled time and real-time distance from the bus.</li>
            <li><strong>3D View:</strong> Tap the "3D" button to tilt the map and view buildings and terrain in three dimensions.</li>
          </ul>
        </section>

        <section className="help-section">
          <h2><Bell size={18} className="help-icon" /> Notifications</h2>
          <p>
            Never miss a bus again. You can enable <strong>Notifications</strong> in Settings to receive notifications.
          </p>
          <ul>
            <li><strong>Pickup Notification:</strong> Get notified as soon as the bus arrives at your selected pickup stop.</li>
            <li><strong>Drop-off Notification:</strong> Receive a notification just before the bus reaches your selected drop-off stop.</li>
          </ul>
        </section>

        <section className="help-section">
          <h2><Bus size={18} className="help-icon" /> Bus Statuses Explained</h2>
          <p>
            You might see different trip statuses at the top of your screen:
          </p>
          <ul>
            <li><strong>Active/In Service:</strong> The bus is on its scheduled route and transmitting live location data.</li>
            <li><strong>Delayed:</strong> The bus is running behind schedule. The app will show an estimate how late it is.</li>
          </ul>
        </section>

        <section className="help-section">
          <h2><Settings size={18} className="help-icon" /> Settings & Preferences</h2>
          <p>
            Tap the <strong>menu icon</strong> (top left) to access Settings, where you can:
          </p>
          <ul>
            <li>Change your default Boarding Stop or Destination Stop.</li>
            <li>Toggle notifications on or off and choose your stops.</li>
          </ul>
        </section>

        <section className="help-section">
          <h2><HelpCircle size={18} className="help-icon" /> Need More Help?</h2>
          <p>If you encounter any problems or have suggestions, open the side menu and tap <strong style={{ whiteSpace: 'nowrap' }}>"Send Feedback."</strong></p>
        </section>

      </div>
    </div>
  );
}
