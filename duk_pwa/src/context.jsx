import React, { createContext, useContext, useReducer, useEffect, useRef } from 'react';
import { globalStore, saveStoreToCache } from './store';

const AppContext = createContext();

export function useAppContext() {
  return useContext(AppContext);
}

const initialState = {
  tripState: globalStore.tripState,
  busPosition: globalStore.busPosition,
  history: globalStore.history,
  stops: globalStore.stops,
  plannedCoords: globalStore.plannedCoords,
  eta: null,
  etaTargetStopId: null,
  mlEtas: null,
  animatedBus: globalStore.busPosition ? [globalStore.busPosition.lon, globalStore.busPosition.lat] : null,
  isLive: false,
};

function appReducer(state, action) {
  switch (action.type) {
    case 'SET_TRIP_STATE':
      globalStore.tripState = action.payload;
      return { ...state, tripState: action.payload };
    case 'SET_BUS_POSITION':
      globalStore.busPosition = action.payload;
      return { ...state, busPosition: action.payload };
    case 'SET_HISTORY':
      globalStore.history = action.payload;
      return { ...state, history: action.payload };
    case 'SET_STOPS':
      globalStore.stops = action.payload;
      return { ...state, stops: action.payload };
    case 'SET_PLANNED_COORDS':
      globalStore.plannedCoords = action.payload;
      return { ...state, plannedCoords: action.payload };
    case 'SET_ETA':
      return { ...state, eta: action.payload };
    case 'SET_ETA_TARGET':
      return { ...state, etaTargetStopId: action.payload };
    case 'SET_ML_ETAS':
      return { ...state, mlEtas: action.payload };
    case 'SET_ANIMATED_BUS':
      return { ...state, animatedBus: action.payload };
    case 'SET_IS_LIVE':
      return { ...state, isLive: action.payload };
    case 'SAVE_CACHE':
      saveStoreToCache();
      return state;
    case 'BATCH_UPDATE':
      return { ...state, ...action.payload };
    default:
      return state;
  }
}

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(appReducer, initialState);

  // Debounced cache saving
  const cacheTimeout = useRef(null);
  useEffect(() => {
    if (cacheTimeout.current) clearTimeout(cacheTimeout.current);
    cacheTimeout.current = setTimeout(() => {
      dispatch({ type: 'SAVE_CACHE' });
    }, 2000);
  }, [state]);

  return (
    <AppContext.Provider value={{ state, dispatch }}>
      {children}
    </AppContext.Provider>
  );
}
