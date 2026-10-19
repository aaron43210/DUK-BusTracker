import { getRouteHistory } from '../api';

const MAX_TRAIL_COORDS = 2000;
export const SNAP_THRESHOLD_M = 300;

export function haversineM(lon1, lat1, lon2, lat2) {
  const R = 6371000;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat/2)**2 + Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLon/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

class TrailManager {
  constructor(mapInstance = null) {
    this.map = mapInstance;
    this.trailCoords = [];
    this.tripId = null;
    this._plannedCoords = [];
    this._lastSnappedIdx = 0;
    this._lastDrawnLength = -1;
    this._lastAnimPathLength = -1;
  }
  setMap(m) { this.map=m; this._lastDrawnLength=-1; this._lastAnimPathLength=-1; this.drawLive([]); }
  setPlannedRoute(c) {
    if (!c||!c.length){this._plannedCoords=[];this._lastSnappedIdx=0;return;}
    const isMulti=Array.isArray(c[0][0]);
    this._plannedCoords=isMulti?c.flat():c.slice();
    this._lastSnappedIdx=0;
  }
  findNearestOnRoute(lon,lat) {
    if(!this._plannedCoords||!this._plannedCoords.length) return null;
    let minD=Infinity,bestIdx=-1;
    const start=Math.max(0,this._lastSnappedIdx-5);
    for(let i=start;i<this._plannedCoords.length;i++){
      const [pl,pa]=this._plannedCoords[i];
      const d=haversineM(lon,lat,pl,pa);
      if(d<minD){minD=d;bestIdx=i;}
    }
    if(bestIdx===-1) return null;
    if(bestIdx>this._lastSnappedIdx) this._lastSnappedIdx=bestIdx;
    return {snapped:this._plannedCoords[bestIdx],idx:bestIdx,distM:minD};
  }
  async init(tripId) {
    if(!tripId) return; this.tripId=tripId;
    try{const data=await getRouteHistory(tripId);if(!data)return;if(data.coordinates&&data.coordinates.length>=2){this.trailCoords=data.coordinates;this._lastDrawnLength=-1;this.drawLive([]);}}catch(e){console.warn('[TrailManager]',e);}
  }
  setHistory(h){if(h&&h.length>0){this.trailCoords=h.slice();this._lastDrawnLength=-1;this.drawLive([]);}}
  commitSegment(path){
    if(!path||path.length<2) return;
    if(this.trailCoords.length>0){const l=this.trailCoords[this.trailCoords.length-1];const f=path[0];if(Math.abs(l[0]-f[0])<0.0001&&Math.abs(l[1]-f[1])<0.0001){this.trailCoords.push(...path.slice(1));}else{this.trailCoords.push(...path);}}else{this.trailCoords.push(...path);}
    if(this.trailCoords.length>MAX_TRAIL_COORDS)this.trailCoords=this.trailCoords.slice(this.trailCoords.length-MAX_TRAIL_COORDS);
    this._lastDrawnLength=-1;this.drawLive([]);
  }
  drawLive(animatingPath){
    if(!this.map) return;const source=this.map.getSource('trail');if(!source)return;
    const hL=this.trailCoords.length,aL=animatingPath?animatingPath.length:0;
    if(hL===this._lastDrawnLength&&aL===this._lastAnimPathLength) return;
    this._lastDrawnLength=hL;this._lastAnimPathLength=aL;
    const fp=this.trailCoords.slice();
    if(animatingPath&&aL>0){if(fp.length>0){const l=fp[fp.length-1];const f=animatingPath[0];if(Math.abs(l[0]-f[0])<0.0001&&Math.abs(l[1]-f[1])<0.0001){fp.push(...animatingPath.slice(1));}else{fp.push(...animatingPath);}}else{fp.push(...animatingPath);}}
    source.setData({type:'Feature',geometry:{type:'LineString',coordinates:fp.length>=2?fp:[]}});
  }
  clear(){this.trailCoords=[];this.tripId=null;this._plannedCoords=[];this._lastSnappedIdx=0;this._lastDrawnLength=-1;this._lastAnimPathLength=-1;this.drawLive([]);}
}
export default TrailManager;
