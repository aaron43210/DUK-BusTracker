// src/pages/Login.jsx
// ============================================================================
// Login page shown before the admin logs in.
// Sends username & password to the backend.
// ============================================================================

import React, { useState } from 'react'; // React + state hook
import { login, setToken } from '../api.js'; // API functions
import { useToast } from '../App.jsx'; // Toast notification

// Login component
// onLogin(token) is received from App.jsx
function Login({ onLogin }) {

  // -------------------------
  // Form State
  // -------------------------

  // Stores the username entered by the user
  const [username, setUsername] = useState('');

  // Stores the password entered by the user
  const [password, setPassword] = useState('');

  // Toast popup function
  const showToast = useToast();

  // True while waiting for login response
  const [loading, setLoading] = useState(false);

  // ==========================================================================
  // Runs when the user submits the login form
  // ==========================================================================
  async function handleSubmit(e) {

    // Stop the page from refreshing
    e.preventDefault();

    // Check if both fields are filled
    if (!username || !password) {
      showToast('Please enter both username and password.', 'error');
      return;
    }

    // Show loading state
    setLoading(true);

    try {

      // Send username & password to backend
      const token = await login(username, password);

      // Save token for future API requests
      setToken(token);

      // Tell App.jsx that login succeeded
      onLogin(token);

    } catch (err) {

      // Show error message if login fails
      showToast(
        err.message || 'Invalid credentials. Please try again.',
        'error'
      );

    } finally {

      // Stop loading whether login succeeds or fails
      setLoading(false);
    }
  }

  // ==========================================================================
  // UI
  // ==========================================================================
  return (

    // Full-screen login page
    <div className="login-page">

      {/* Login box */}
      <div className="login-card">

        {/* Logo and title */}
        <div className="login-logo">

          {/* University logo */}
          <img
            src="/src/assets/duk_logo.png"
            alt="Digital University Kerala"
            style={{

              // Make image a block element
              display: 'block',

              // Center the image
              margin: '0 auto',

              // Maximum image width
              maxWidth: '160px',

              // Keep image ratio correct
              height: 'auto',

              // Space below image
              marginBottom: '16px',
            }}
          />

          {/* Heading */}
          <div className="login-title">
            DUK Bus Admin
          </div>

          {/* Small description */}
          <div className="login-subtitle">
            Digital University Kerala — Admin Portal
          </div>

        </div>

        {/* Login form */}
        <form onSubmit={handleSubmit}>

          {/* ================= Username ================= */}

          <div className="form-group">

            {/* Username label */}
            <label
              className="form-label"
              htmlFor="username"
            >
              Username
            </label>

            {/* Username input */}
            <input
              id="username"
              type="text"
              className="form-input"
              placeholder="Username"

              // Current username value
              value={username}

              // Update username as user types
              onChange={e => setUsername(e.target.value)}

              // Browser autofill
              autoComplete="username"

              // Cursor starts here
              autoFocus
            />

          </div>

          {/* ================= Password ================= */}

          <div className="form-group">

            {/* Password label */}
            <label
              className="form-label"
              htmlFor="password"
            >
              Password
            </label>

            {/* Password input */}
            <input
              id="password"
              type="password"

              // Hide typed characters
              className="form-input"

              placeholder="Password"

              // Current password value
              value={password}

              // Update password while typing
              onChange={e => setPassword(e.target.value)}

              // Browser password autofill
              autoComplete="current-password"
            />

          </div>

          {/* ================= Login Button ================= */}

          <button

            // Clicking submits the form
            type="submit"

            className="login-btn"

            // Disable while waiting
            disabled={loading}
          >

            {/* Change button text while loading */}
            {
              loading
                ? 'Signing in…'
                : 'Sign In'
            }

          </button>

        </form>

        {/* Footer message */}
        <p
          style={{
            textAlign: 'center',
            marginTop: '20px',
            fontSize: '12px',
            color: 'var(--text-muted)'
          }}
        >
          Access restricted to DUK administrators only.
        </p>

      </div>

    </div>
  );
}

// Make this component available in other files
export default Login;
