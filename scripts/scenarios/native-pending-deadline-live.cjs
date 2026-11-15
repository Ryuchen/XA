// Historical entry point; executes current production React controllers + HTTP.
import('../fronted-pending-deadline-contract.mjs').catch(error => {
  console.error(error);
  process.exitCode = 1;
});
