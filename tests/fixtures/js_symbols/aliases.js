// Canonical class + const alias + destructured re-export (tier-A JS golden)
class SemanticCodeNavigatorService {
  navigate(path) {
    return path;
  }
}

const CodeNavigator = SemanticCodeNavigatorService;

module.exports = {
  CodeNavigator,
  SemanticCodeNavigatorService,
};
