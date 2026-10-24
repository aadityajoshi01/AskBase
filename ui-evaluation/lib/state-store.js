export const initialAnalysisState = Object.freeze({
  requestId: 0,
  status: 'idle',
  result: null,
  error: null,
});

export function analysisReducer(state, action) {
  if (action.type === 'analysis/requested') return { ...state, requestId: action.requestId, status: 'loading', result: null, error: null };
  if (action.type === 'analysis/resolved' && action.requestId === state.requestId) return { ...state, status: 'ready', result: action.result, error: null };
  if (action.type === 'analysis/rejected' && action.requestId === state.requestId) return { ...state, status: 'error', result: null, error: action.error };
  if (action.type === 'analysis/reset') return { ...initialAnalysisState, requestId: state.requestId };
  return state;
}

export function createStateStore(reducer = analysisReducer, initialState = initialAnalysisState) {
  let state = initialState;
  const listeners = new Set();
  return {
    getState: () => state,
    dispatch(action) {
      const nextState = reducer(state, action);
      if (nextState !== state) {
        state = nextState;
        for (const listener of listeners) listener(state, action);
      }
      return state;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
