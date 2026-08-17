import React, { useContext, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, MapPin, Bell, Settings, Search } from 'lucide-react';
import { SplashContext } from '../App';
import TopBar from '../components/TopBar';

export default function Help() {
  const navigate = useNavigate();
  const { setSplashReady } = useContext(SplashContext);

  useEffect(() => {
    setSplashReady();
  }, [setSplashReady]);

  return (
    <div className="help-screen">
      <TopBar
        showBack
        onBack={() => navigate(-1)}
        title="Help & Guidelines"
      />

      <div className="help-content">
        <section className="help-section">
          <h2><MapPin size={18} className="help-icon" /> Live Tracking & Map</h2>
          <p>
            Welcome to the DUK Bus Tracker! Use the map on the main screen to track the live location of college buses.
            Tap on any bus stop icon on the map to see which buses pass through it and their scheduled times.
          </p>
        </section>

        <section className="help-section">
          <h2><Search size={18} className="help-icon" /> Finding Routes & Stops</h2>
          <p>
            Use the search bar at the bottom to find a specific stop or bus route. The map will automatically focus on your selection so you can easily see its location.
          </p>
        </section>

        <section className="help-section">
          <h2><Bell size={18} className="help-icon" /> Bus Statuses Explained</h2>
          <p>
            When checking a bus or route, you might see different statuses:
          </p>
          <ul>
            <li><strong>In Service:</strong> The bus is currently active on its route, and its live location is available.</li>
            <li><strong>Not in Service:</strong> The bus is not currently active. It may have completed its trips for the day or hasn't started yet.</li>
            <li><strong>Unscheduled:</strong> The bus doesn't have an active trip scheduled for the current time window (Morning/Evening).</li>
          </ul>
        </section>

        <section className="help-section">
          <h2><Settings size={18} className="help-icon" /> Settings & Preferences</h2>
          <p>
            Access the menu in the top left to manage your profile, toggle between Morning and Evening trip schedules, and adjust map settings.
          </p>
        </section>
      </div>
    </div>
  );
}
