//! Arena of nodes over byte ranges. Leaves in order reproduce the source.

pub const NONE: u32 = u32::MAX;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Node {
    pub kind: u16,
    pub start: u32,
    pub end: u32,
    pub first_child: u32,
    pub next_sibling: u32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Tree {
    pub root: u32,
    pub nodes: Vec<Node>,
}

impl Tree {
    pub fn node(&self, id: u32) -> &Node {
        &self.nodes[id as usize]
    }

    pub fn render(&self, source: &str) -> String {
        let mut out = String::with_capacity(source.len());
        self.write(self.root, source, &mut out);
        out
    }

    fn write(&self, id: u32, source: &str, out: &mut String) {
        let node = self.node(id);
        if node.first_child == NONE {
            let start = node.start as usize;
            let end = node.end as usize;
            if start < end && end <= source.len() {
                out.push_str(&source[start..end]);
            }
            return;
        }
        let mut child = node.first_child;
        while child != NONE {
            self.write(child, source, out);
            child = self.node(child).next_sibling;
        }
    }

    pub fn children(&self, id: u32) -> Vec<u32> {
        let mut out = Vec::new();
        let mut child = self.node(id).first_child;
        while child != NONE {
            out.push(child);
            child = self.node(child).next_sibling;
        }
        out
    }
}

#[derive(Clone, Debug)]
pub struct Token {
    pub kind: u16,
    pub start: u32,
    pub end: u32,
}

pub struct Builder {
    nodes: Vec<Node>,
    open: Vec<Open>,
}

struct Open {
    id: u32,
    last_child: u32,
}

impl Builder {
    pub fn new() -> Builder {
        Builder {
            nodes: Vec::new(),
            open: Vec::new(),
        }
    }

    pub fn start(&mut self, kind: u16, start: u32) -> u32 {
        let id = self.nodes.len() as u32;
        self.nodes.push(Node {
            kind,
            start,
            end: start,
            first_child: NONE,
            next_sibling: NONE,
        });
        self.add_child(id);
        self.open.push(Open {
            id,
            last_child: NONE,
        });
        id
    }

    pub fn leaf(&mut self, kind: u16, start: u32, end: u32) {
        let id = self.nodes.len() as u32;
        self.nodes.push(Node {
            kind,
            start,
            end,
            first_child: NONE,
            next_sibling: NONE,
        });
        self.add_child(id);
    }

    pub fn finish(&mut self, end: u32) -> u32 {
        let open = self.open.pop().expect("finish without start");
        self.nodes[open.id as usize].end = end;
        open.id
    }

    pub fn tree(self) -> Tree {
        Tree {
            root: 0,
            nodes: self.nodes,
        }
    }

    fn add_child(&mut self, child: u32) {
        let Some(parent_index) = self.open.len().checked_sub(1) else {
            return;
        };
        let parent_id = self.open[parent_index].id;
        let last = self.open[parent_index].last_child;
        if last == NONE {
            self.nodes[parent_id as usize].first_child = child;
        } else {
            self.nodes[last as usize].next_sibling = child;
        }
        self.open[parent_index].last_child = child;
    }
}
