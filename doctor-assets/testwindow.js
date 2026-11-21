// no-errands doctor test window for macOS (JXA). Run: osascript -l JavaScript testwindow.js <title>
// A native Cocoa window owned by osascript itself (no Automation/System Events permission needed):
// one text field + an OK button. Pressing OK prints the field's text to stdout and exits.
ObjC.import('Cocoa');
ObjC.import('stdlib');
var field = null;

ObjC.registerSubclass({
  name: 'NEDoctorTarget',
  methods: {
    'clicked:': {
      types: ['void', ['id']],
      implementation: function (sender) {
        var s = field.stringValue.js + '\n';
        $.NSFileHandle.fileHandleWithStandardOutput.writeData($(s).dataUsingEncoding($.NSUTF8StringEncoding));
        $.exit(0);
      }
    }
  }
});

function run(argv) {
  var title = argv.length > 0 ? argv[0] : 'NE-DOCTOR';
  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyRegular);
  var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(
    $.NSMakeRect(200, 200, 380, 120), $.NSWindowStyleMaskTitled, $.NSBackingStoreBuffered, false);
  win.title = title;
  field = $.NSTextField.alloc.initWithFrame($.NSMakeRect(20, 60, 230, 24));
  field.setAccessibilityLabel('NE input');
  win.contentView.addSubview(field);
  var target = $.NEDoctorTarget.alloc.init;
  var btn = $.NSButton.alloc.initWithFrame($.NSMakeRect(262, 56, 96, 32));
  btn.title = 'OK';
  btn.bezelStyle = $.NSBezelStyleRounded;
  btn.target = target;
  btn.action = 'clicked:';
  win.contentView.addSubview(btn);
  win.makeKeyAndOrderFront(null);
  app.run;
}
