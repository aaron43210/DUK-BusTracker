/**
 * BusIdleAnimation.jsx
 */
export default function BusIdleAnimation({ nextTripTime, isUnscheduled }) {
  return (
    <div className="bus-idle-wrap">
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '8px', borderRadius: '12px', overflow: 'hidden', border: '1px solid #eaeaea', background: '#fff' }}>
        <img 
          src="https://cdn.dribbble.com/userupload/20958845/file/original-c75e24374e4b3f92a6a5240b3ca7a60c.gif" 
          alt="Bus Moving" 
          style={{ width: '100%', maxWidth: '100%', height: 'auto', objectFit: 'cover' }} 
        />
      </div>
    </div>
  );
}
