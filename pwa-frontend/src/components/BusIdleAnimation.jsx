/**
 * BusIdleAnimation.jsx
 */
export default function BusIdleAnimation({ nextTripTime, isUnscheduled }) {
  return (
    <div className="bus-idle-wrap">
      <div className="bus-idle-road">
        <div className="bus-idle-scenery bus-idle-scenery--left">🌴</div>
        <div className="bus-idle-scenery bus-idle-scenery--right">🌴</div>
        <div className="bus-idle-bus">🚌</div>
      </div>
      <p className="bus-idle-label">
        {isUnscheduled ? 'Bus is Moving (Unscheduled)' : 'Not in Service'}
      </p>
      {nextTripTime && !isUnscheduled && (
        <p className="bus-idle-next">Next trip: {nextTripTime}</p>
      )}
    </div>
  );
}
