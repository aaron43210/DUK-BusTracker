/**
 * OtpVerify.jsx — DUK Bus Tracker PWA
 * Screen 2: 6-digit OTP entry.
 * Calls POST /auth/verify, saves JWT, navigates to /route.
 */
import React, { useState, useRef, useEffect, useContext } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { verify, register } from '../api';
import { setApiToken } from '../api';
import { saveToken, saveUser } from '../storage';
import { useToast, SplashContext } from '../App';
import TopBar from '../components/TopBar';
import { requestAndSaveFcmToken } from '../firebase';

const CODE_LENGTH = 6;

export default function OtpVerify() {
  const navigate  = useNavigate();
  const location  = useLocation();
  const showToast = useToast();

  const { email = '', name = '', boardingStop = null } = location.state || {};
  
  const { setSplashReady } = useContext(SplashContext);
  useEffect(() => {
    setSplashReady();
  }, [setSplashReady]);

  const [otp,         setOtp]         = useState(Array(CODE_LENGTH).fill(''));
  const [error,       setError]       = useState('');
  const [resendTimer, setResendTimer] = useState(30);
  const [verified,    setVerified]    = useState(false);
  const [verifying,   setVerifying]   = useState(false);
  const [resending,   setResending]   = useState(false);
  const [shaking,     setShaking]     = useState(false);

  const inputRefs = useRef(Array(CODE_LENGTH).fill(null));

  // Countdown for resend button
  useEffect(() => {
    if (resendTimer <= 0) return;
    const id = setInterval(() => setResendTimer(t => t - 1), 1000);
    return () => clearInterval(id);
  }, [resendTimer]);

  // Auto-navigate after verified
  useEffect(() => {
    if (verified) {
      const t = setTimeout(() => navigate('/route', { replace: true }), 800);
      return () => clearTimeout(t);
    }
  }, [verified, navigate]);

  const triggerShake = () => {
    setShaking(true);
    setTimeout(() => setShaking(false), 500);
  };

  const handleChange = (text, index) => {
    setError('');
    const digit = text.replace(/[^0-9]/g, '').slice(-1);
    const next  = [...otp];
    next[index] = digit;
    setOtp(next);
    if (digit && index < CODE_LENGTH - 1) {
      inputRefs.current[index + 1]?.focus();
    }
    if (!digit && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
    // Auto-submit when all 6 filled
    if (digit && index === CODE_LENGTH - 1) {
      const full = [...next].join('');
      if (full.length === CODE_LENGTH) {
        setTimeout(() => submitOtp(full), 80);
      }
    }
  };

  const handleKeyDown = (e, index) => {
    if (e.key === 'Backspace' && !otp[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
  };

  const handlePaste = (e) => {
    const pasted = e.clipboardData.getData('text').replace(/[^0-9]/g, '').slice(0, CODE_LENGTH);
    if (pasted.length === CODE_LENGTH) {
      const arr = pasted.split('');
      setOtp(arr);
      inputRefs.current[CODE_LENGTH - 1]?.focus();
      submitOtp(pasted);
    }
  };

  const submitOtp = async (code) => {
    if (verifying) return;
    setVerifying(true);
    setError('');
    try {
      const res = await verify(email, code);
      // res = { access_token, token_type, user }
      setApiToken(res.access_token);
      saveToken(res.access_token);
      saveUser(res.user);
      // Request notification permission and register the FCM device token.
      // Fire-and-forget: do not block the login flow.
      requestAndSaveFcmToken().catch(() => {});
      setVerified(true);
    } catch (err) {
      setError(err.message || 'Invalid code. Try again.');
      triggerShake();
      setOtp(Array(CODE_LENGTH).fill(''));
      setTimeout(() => inputRefs.current[0]?.focus(), 50);
    } finally {
      setVerifying(false);
    }
  };

  const handleResend = async () => {
    if (resendTimer > 0 || resending) return;
    setResending(true);
    try {
      await register(name, email, boardingStop?.id);
      setResendTimer(30);
      showToast('OTP resent to your email!', 'success');
    } catch (err) {
      showToast('Failed to resend. Try again.', 'error');
    } finally {
      setResending(false);
    }
  };

  const codeComplete = otp.join('').length === CODE_LENGTH;

  if (verified) {
    return (
      <div className="auth-screen screen">
        <div className="success-check">✅</div>
        <div className="success-title">Verified!</div>
        <div className="success-subtitle">Taking you to the tracker…</div>
      </div>
    );
  }

  return (
    <>
      <TopBar
        showBack
        onBack={() => navigate('/')}
        title="Verify Email"
      />
      <div className="auth-screen screen" style={{ paddingTop: '24px' }}>

        <div className="auth-screen__logo">📬</div>
        <h1 className="auth-screen__title">Check your email</h1>
        <p className="auth-screen__subtitle">
          We sent a 6-digit code to<br />
          <strong style={{ color: 'var(--mint-text)' }}>{email}</strong>
        </p>

        <div className="auth-card">

          <div className={`otp-inputs ${shaking ? 'otp-shake' : ''}`} onPaste={handlePaste}>
            {otp.map((digit, i) => (
              <input
                key={i}
                id={`otp-${i}`}
                ref={el => { inputRefs.current[i] = el; }}
                className={`otp-input${digit ? ' otp-input--filled' : ''}${error ? ' otp-input--error' : ''}`}
                type="tel"
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={1}
                value={digit}
                onChange={e => handleChange(e.target.value, i)}
                onKeyDown={e => handleKeyDown(e, i)}
                autoFocus={i === 0}
                autoComplete={i === 0 ? 'one-time-code' : 'off'}
              />
            ))}
          </div>

          {error && (
            <p className="field-error" style={{ textAlign: 'center' }}>{error}</p>
          )}

          <button
            id="verify-btn"
            className="btn btn--primary"
            onClick={() => submitOtp(otp.join(''))}
            disabled={!codeComplete || verifying}
          >
            {verifying
              ? 'Verifying…'
              : <><span>Verify & Continue</span> <ArrowRight size={18} /></>
            }
          </button>

          <p className="otp-resend">
            {resendTimer > 0
              ? <>Resend in <strong>{resendTimer}s</strong></>
              : <span
                  className="otp-resend__link"
                  role="button"
                  tabIndex={0}
                  onClick={handleResend}
                  onKeyDown={e => e.key === 'Enter' && handleResend()}
                >
                  {resending ? 'Sending…' : 'Resend OTP'}
                </span>
            }
          </p>

        </div>
      </div>
    </>
  );
}
