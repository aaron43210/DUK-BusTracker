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
    <div className="help-screen" style={{ height: '100%', overflowY: 'auto' }}>
      <TopBar
        showBack
        onBack={() => navigate(-1)}
        title="Help & Guidelines"
      />

      <div className="help-content" style={{ padding: '16px', paddingBottom: '40px' }}>
        
        <section className="help-section">
          <h2><ShieldCheck size={18} className="help-icon" /> Getting Started</h2>
          <p>
            Welcome to the <strong>DUK Bus Tracker</strong>! This app is designed exclusively for Digital University Kerala students to effortlessly track college buses, predict arrivals, and receive smart alerts.
          </p>
          <p>
            Upon your first login, you verified your identity using your <strong>@duk.ac.in</strong> email and an OTP. You also selected your default boarding/destination stops, which helps the app provide personalized ETAs.
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
            <li><strong>Pull to Refresh:</strong> Swipe down on the timeline to sync the latest data manually.</li>
          </ul>
        </section>

        <section className="help-section">
          <h2><MapPin size={18} className="help-icon" /> Map View & 3D Mode</h2>
          <p>
            Switch to the <strong>Map View</strong> to see the bus on an interactive map.
          </p>
          <ul>
            <li><strong>Auto-Follow:</strong> The map centers automatically on the bus as it moves. If you pan away, tap the <strong>crosshair icon</strong> to recenter.</li>
            <li><strong>Interactive Stops:</strong> Tap any stop on the map to see its scheduled time and real-time distance from the bus.</li>
            <li><strong>3D View:</strong> Tap the "3D" button to tilt the map and view buildings and terrain in three dimensions.</li>
          </ul>
        </section>

        <section className="help-section">
          <h2><Bell size={18} className="help-icon" /> Smart Alerts</h2>
          <p>
            Never miss a bus again. You can enable <strong>Smart Alerts</strong> in Settings to receive proximity notifications.
          </p>
          <ul>
            <li><strong>Catch the Bus:</strong> Get alerted exactly 10 minutes before the bus arrives at your boarding stop.</li>
            <li><strong>Get-Off Trigger:</strong> Receive a reminder to gather your belongings when the bus is 1km away from your destination.</li>
          </ul>
        </section>

        <section className="help-section">
          <h2><Bus size={18} className="help-icon" /> Bus Statuses Explained</h2>
          <p>
            You might see different trip statuses at the top of your screen:
          </p>
          <ul>
            <li><strong>Active/In Service:</strong> The bus is on its scheduled route and transmitting live GPS data.</li>
            <li><strong>Delayed:</strong> The bus is running behind schedule. The app will show exactly how many minutes late it is.</li>
            <li><strong>Unscheduled Trip:</strong> The bus is moving but not on a standard morning/evening route (e.g., a special trip).</li>
            <li><strong>Waiting for Service / Trip Completed:</strong> The bus is not currently active. The app will show the time of the next scheduled trip.</li>
            <li><strong>Weekend (No Service):</strong> Displayed on Saturdays and Sundays when standard routes are paused.</li>
          </ul>
        </section>

        <section className="help-section">
          <h2><Settings size={18} className="help-icon" /> Settings & Preferences</h2>
          <p>
            Tap the <strong>menu icon</strong> (top left) to access Settings, where you can:
          </p>
          <ul>
            <li>Change your default Boarding Stop or Destination Stop.</li>
            <li>Toggle Smart Alerts and configure custom trigger stops.</li>
            <li>Log out or re-verify your device.</li>
          </ul>
        </section>
        
        <section className="help-section">
          <h2><HelpCircle size={18} className="help-icon" /> Need More Help?</h2>
          <p>
            If you encounter bugs or have suggestions, open the side menu and tap <strong>"Send Feedback"</strong>. 
            For technical issues with the tracking hardware on the bus, please contact the transport administrator.
          </p>
        </section>

      </div>
    </div>
  );
}
